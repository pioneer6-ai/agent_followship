"""
Channel providers: the deterministic, provider-specific send implementations.

Each provider's only job is "hand this message to the API and report exactly
what happened". It never decides, never retries across channels and -- above
all -- never raises: any failure is converted into a
:class:`ProviderOutcome` carrying a normalized :class:`SendErrorCode` plus the
verbatim provider code.

Supported channels:

==============  ==========================================================
``whatsapp``    Meta WhatsApp Business Cloud API, or Twilio WhatsApp
``sms``         Twilio Programmable Messaging
``email``       SMTP (works with plain SMTP, Amazon SES SMTP, Mailgun, ...)
==============  ==========================================================

AWS End User Messaging uses an "AWS-provider" signature that differs from both
implementations below; it is intentionally left out so that no untested boto3
path ships. :class:`MessageProvider` is the only interface to satisfy.
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Any, Callable, Dict, List, Optional
import base64
import json
import smtplib
import socket
import ssl

from tools.config import MessagingConfig
from tools.errors import (
    META_ERROR_CODES,
    TWILIO_ERROR_CODES,
    SendErrorCode,
    from_http_status,
)
from tools.transport import HttpResponse, HttpTransport, UrllibTransport


@dataclass
class SendRequest:
    """
    A channel-agnostic outbound message.

    Attributes:
        to: Normalized destination (E.164 phone number or email address).
        template: Approved template name, when the message is templated.
        params: Positional template parameters, in template order.
        body: Pre-rendered text (used for SMS/email and free-form WhatsApp).
        subject: Email subject line.
        language: Template language code.
    """

    to: str
    template: Optional[str] = None
    params: List[str] = field(default_factory=list)
    body: Optional[str] = None
    subject: Optional[str] = None
    language: str = "en"


@dataclass
class ProviderOutcome:
    """
    Raw result of one provider call, before it becomes a :class:`ToolResult`.

    Attributes:
        success: Whether the provider accepted the message.
        message_id: Provider-assigned identifier.
        error_code: Normalized error classification on failure.
        error_message: Provider-supplied description.
        provider_code: Raw provider error code, preserved verbatim.
        simulated: True when the call was short-circuited (dry-run).
        latency_ms: Duration of the provider round trip.
        raw: Raw response body, kept for debugging/auditing.
    """

    success: bool
    message_id: Optional[str] = None
    error_code: Optional[SendErrorCode] = None
    error_message: Optional[str] = None
    provider_code: Optional[str] = None
    simulated: bool = False
    latency_ms: int = 0
    raw: Optional[Dict[str, Any]] = None


class MessageProvider(ABC):
    """
    Abstract outbound channel.

    Implementations must be total functions: every input produces either a
    successful outcome or a failed outcome, never an exception.
    """

    #: Logical channel name reported to the LLM.
    channel: str = "unknown"

    @abstractmethod
    def is_configured(self) -> bool:
        """Whether this channel has enough configuration to send for real."""

    @abstractmethod
    def send(self, request: SendRequest) -> ProviderOutcome:
        """Deliver the message, classifying any failure."""

    def _guard(self, exc: BaseException) -> ProviderOutcome:
        """Convert an unexpected exception into a normalized failure."""
        return ProviderOutcome(
            success=False,
            error_code=SendErrorCode.UNKNOWN,
            error_message=f"Unexpected {type(exc).__name__}: {exc}",
            provider_code=type(exc).__name__,
        )


class MetaWhatsAppProvider(MessageProvider):
    """
    WhatsApp via the Meta WhatsApp Business Cloud API.

    Posts to ``https://graph.facebook.com/{version}/{phone_number_id}/messages``.

    Business-initiated conversations must use an approved template once the
    24-hour customer-service window has closed, so templated sends are the
    primary path; free-form text is supported for in-window replies.
    """

    channel = "whatsapp"
    BASE_URL = "https://graph.facebook.com"

    def __init__(
        self,
        config: MessagingConfig,
        transport: Optional[HttpTransport] = None,
    ) -> None:
        """
        Args:
            config: Messaging configuration.
            transport: HTTP transport (defaults to the real urllib one).
        """
        self.config = config
        self.transport: HttpTransport = transport or UrllibTransport()

    def is_configured(self) -> bool:
        """True when an access token and phone number id are present."""
        return bool(self.config.meta_access_token and self.config.meta_phone_number_id)

    @property
    def _endpoint(self) -> str:
        """Fully-qualified messages endpoint."""
        return (
            f"{self.BASE_URL}/{self.config.meta_api_version}/"
            f"{self.config.meta_phone_number_id}/messages"
        )

    def _build_payload(self, request: SendRequest) -> Dict[str, Any]:
        """Build the Cloud API request body."""
        # Meta expects the number without the leading '+'.
        payload: Dict[str, Any] = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": request.to.lstrip("+"),
        }

        if request.template:
            template: Dict[str, Any] = {
                "name": request.template,
                "language": {"code": request.language or "en"},
            }
            if request.params:
                template["components"] = [
                    {
                        "type": "body",
                        "parameters": [
                            {"type": "text", "text": str(p)} for p in request.params
                        ],
                    }
                ]
            payload["type"] = "template"
            payload["template"] = template
        else:
            payload["type"] = "text"
            payload["text"] = {"preview_url": False, "body": request.body or ""}

        return payload

    def send(self, request: SendRequest) -> ProviderOutcome:
        """Send the message through the Cloud API."""
        try:
            configured = self.is_configured()
            if self.config.live_requested and not configured:
                return ProviderOutcome(
                    success=False,
                    error_code=SendErrorCode.CONFIG_MISSING,
                    error_message=(
                        "META_WHATSAPP_ACCESS_TOKEN / "
                        "META_WHATSAPP_PHONE_NUMBER_ID are not set"
                    ),
                    provider_code="config",
                )
            if not self.config.live_requested:
                return ProviderOutcome(
                    success=True,
                    message_id="simulated-whatsapp",
                    simulated=True,
                )

            headers = {
                "Authorization": f"Bearer {self.config.meta_access_token}",
                "Content-Type": "application/json",
            }
            response = self.transport.post_json(
                self._endpoint,
                headers=headers,
                payload=self._build_payload(request),
                timeout=self.config.timeout_seconds,
            )
            return self._classify(response)
        except Exception as exc:  # pragma: no cover - defensive
            return self._guard(exc)

    def _classify(self, response: HttpResponse) -> ProviderOutcome:
        """Turn a Cloud API response into a normalized outcome."""
        if response.status_code == 0:
            return ProviderOutcome(
                success=False,
                error_code=SendErrorCode.NETWORK_ERROR,
                error_message=response.error or "connection failed",
                provider_code="transport",
                latency_ms=response.latency_ms,
            )

        body = response.json_body()
        error = body.get("error") if isinstance(body, dict) else None

        if response.ok and not error:
            messages = body.get("messages") or []
            message_id = None
            if isinstance(messages, list) and messages:
                message_id = messages[0].get("id")
            return ProviderOutcome(
                success=True,
                message_id=message_id,
                latency_ms=response.latency_ms,
                raw=body or None,
            )

        # Failure: prefer Meta's numeric code, then fall back to HTTP status.
        raw_code = None
        message = None
        if isinstance(error, dict):
            raw_code = error.get("code")
            message = error.get("message") or error.get("error_user_msg")
        if message is None:
            message = response.text() or f"HTTP {response.status_code}"

        error_code: Optional[SendErrorCode] = None
        if isinstance(raw_code, int):
            error_code = META_ERROR_CODES.get(raw_code)
        if error_code is None:
            # A 404 on the messages endpoint is almost always a bad
            # phone_number_id rather than a request problem.
            if response.status_code == 404 and raw_code is None:
                error_code = SendErrorCode.CONFIG_MISSING
            else:
                error_code = from_http_status(response.status_code)

        return ProviderOutcome(
            success=False,
            error_code=error_code,
            error_message=str(message),
            provider_code=str(raw_code) if raw_code is not None else str(response.status_code),
            latency_ms=response.latency_ms,
            raw=body or None,
        )


class TwilioProvider(MessageProvider):
    """
    Twilio Programmable Messaging, for WhatsApp (``whatsapp:+...``) and SMS.

    WhatsApp is addressed with a ``whatsapp:`` prefix on both ends. Templated
    WhatsApp sends prefer Twilio's Content API (``ContentSid`` +
    ``ContentVariables``); when no ContentSid is mapped for a template the
    provider falls back to a free-form ``Body``, which Twilio accepts inside the
    24-hour window and rejects with 63016 outside it. Set
    ``TWILIO_REQUIRE_CONTENT_SID=1`` to make a missing mapping a hard
    ``template_not_found`` instead.
    """

    BASE_URL = "https://api.twilio.com/2010-04-01/Accounts"
    _CHANNEL_PREFIX = "whatsapp:"

    def __init__(
        self,
        config: MessagingConfig,
        *,
        channel: str = "sms",
        transport: Optional[HttpTransport] = None,
    ) -> None:
        """
        Args:
            config: Messaging configuration.
            channel: ``"sms"`` or ``"whatsapp"``.
            transport: HTTP transport (defaults to the real urllib one).
        """
        self.config = config
        self.channel = channel
        self.transport: HttpTransport = transport or UrllibTransport()

    def is_configured(self) -> bool:
        """True when SID, auth token and a sender address are present."""
        return bool(
            self.config.twilio_account_sid
            and self.config.twilio_auth_token
            and self._from_address()
        )

    def _from_address(self) -> Optional[str]:
        """Return the configured sender for this channel."""
        if self.channel == "whatsapp":
            return self.config.twilio_whatsapp_from
        return self.config.twilio_sms_from

    def _addressed_to(self, request: SendRequest) -> str:
        """Apply the ``whatsapp:`` prefix to the destination when needed."""
        if self.channel == "whatsapp" and not request.to.startswith(
            self._CHANNEL_PREFIX
        ):
            return f"{self._CHANNEL_PREFIX}{request.to}"
        return request.to

    def send(self, request: SendRequest) -> ProviderOutcome:
        """Send via the Twilio Messages API."""
        try:
            configured = self.is_configured()
            if self.config.live_requested and not configured:
                return ProviderOutcome(
                    success=False,
                    error_code=SendErrorCode.CONFIG_MISSING,
                    error_message=(
                        "TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN / sender address "
                        "are not set"
                    ),
                    provider_code="config",
                )
            if not self.config.live_requested:
                return ProviderOutcome(
                    success=True,
                    message_id="simulated-" + self.channel,
                    simulated=True,
                )

            form = self._build_form(request)
            if form is None:
                return ProviderOutcome(
                    success=False,
                    error_code=SendErrorCode.TEMPLATE_NOT_FOUND,
                    error_message=(
                        f"No Twilio ContentSid mapped for template "
                        f"'{request.template}'"
                    ),
                    provider_code="content_sid",
                )

            credentials = (
                f"{self.config.twilio_account_sid}:{self.config.twilio_auth_token}"
            )
            token = base64.b64encode(credentials.encode("utf-8")).decode("ascii")
            headers = {
                "Authorization": f"Basic {token}",
                "Content-Type": "application/x-www-form-urlencoded",
            }
            url = (
                f"{self.BASE_URL}/{self.config.twilio_account_sid}/Messages.json"
            )
            response = self.transport.post_form(
                url,
                headers=headers,
                data=form,
                timeout=self.config.timeout_seconds,
            )
            return self._classify(response)
        except Exception as exc:  # pragma: no cover - defensive
            return self._guard(exc)

    def _build_form(self, request: SendRequest) -> Optional[Dict[str, str]]:
        """
        Build the urlencoded request body.

        Returns:
            The form fields, or ``None`` when a required ContentSid mapping is
            missing.
        """
        from_address = self._from_address() or ""
        if self.channel == "whatsapp" and not from_address.startswith(
            self._CHANNEL_PREFIX
        ):
            from_address = f"{self._CHANNEL_PREFIX}{from_address}"

        form: Dict[str, str] = {
            "To": self._addressed_to(request),
            "From": from_address,
        }

        content_sid = (
            self.config.twilio_content_sids.get(request.template)
            if request.template
            else None
        )

        if content_sid:
            form["ContentSid"] = content_sid
            form["ContentVariables"] = _content_variables(request.params)
        elif request.template and self.config.twilio_require_content_sid:
            return None
        else:
            form["Body"] = request.body or ""

        return form

    def _classify(self, response: HttpResponse) -> ProviderOutcome:
        """Turn a Twilio response into a normalized outcome."""
        if response.status_code == 0:
            return ProviderOutcome(
                success=False,
                error_code=SendErrorCode.NETWORK_ERROR,
                error_message=response.error or "connection failed",
                provider_code="transport",
                latency_ms=response.latency_ms,
            )

        body = response.json_body()
        if response.ok and body.get("sid"):
            return ProviderOutcome(
                success=True,
                message_id=body.get("sid"),
                latency_ms=response.latency_ms,
                raw=body,
            )

        raw_code = body.get("code") if isinstance(body, dict) else None
        message = body.get("message") if isinstance(body, dict) else None
        if message is None:
            message = response.text() or f"HTTP {response.status_code}"

        error_code: Optional[SendErrorCode] = None
        if isinstance(raw_code, int):
            error_code = TWILIO_ERROR_CODES.get(raw_code)
        if error_code is None:
            error_code = from_http_status(response.status_code)

        return ProviderOutcome(
            success=False,
            error_code=error_code,
            error_message=str(message),
            provider_code=str(raw_code) if raw_code is not None else str(response.status_code),
            latency_ms=response.latency_ms,
            raw=body or None,
        )


def _content_variables(params: List[str]) -> str:
    """Build Twilio's 1-indexed ``ContentVariables`` JSON object."""
    return json.dumps({str(i): str(p) for i, p in enumerate(params or [], start=1)})


class SmtpEmailProvider(MessageProvider):
    """
    Email over SMTP.

    Works unchanged against a plain MTA, Amazon SES's SMTP endpoint, Mailgun,
    Postmark and friends, so no vendor SDK is required.

    The connection is created through an injectable ``connection_factory``
    which makes every failure mode (auth rejected, recipient refused, host
    unreachable) testable offline.
    """

    channel = "email"

    def __init__(
        self,
        config: MessagingConfig,
        connection_factory: Optional[Callable[[], Any]] = None,
    ) -> None:
        """
        Args:
            config: Messaging configuration.
            connection_factory: Builds an SMTP-like connection object. Defaults
                to a real ``smtplib`` connection honoring the config.
        """
        self.config = config
        self._connection_factory = connection_factory or self._default_factory

    def is_configured(self) -> bool:
        """True when a host and sender address are present."""
        return bool(self.config.smtp_host and self.config.email_from)

    def _default_factory(self) -> Any:
        """Create a real SMTP connection object (not yet connected)."""
        if self.config.smtp_use_ssl:
            return smtplib.SMTP_SSL(
                timeout=self.config.timeout_seconds,
                context=ssl.create_default_context(),
            )
        return smtplib.SMTP(timeout=self.config.timeout_seconds)

    def _build_message(self, request: SendRequest) -> EmailMessage:
        """Build the RFC 5322 message."""
        message = EmailMessage()
        message["To"] = request.to
        message["From"] = self.config.email_from or ""
        message["Subject"] = request.subject or "Message from your dental clinic"
        message.set_content(request.body or "")
        return message

    def send(self, request: SendRequest) -> ProviderOutcome:
        """Deliver the email, classifying any SMTP failure."""
        try:
            configured = self.is_configured()
            if self.config.live_requested and not configured:
                return ProviderOutcome(
                    success=False,
                    error_code=SendErrorCode.CONFIG_MISSING,
                    error_message="SMTP_HOST / EMAIL_FROM are not set",
                    provider_code="config",
                )
            if not self.config.live_requested:
                return ProviderOutcome(
                    success=True,
                    message_id="simulated-email",
                    simulated=True,
                )

            message = self._build_message(request)
            connection = self._connection_factory()

            try:
                connection.connect(self.config.smtp_host, self.config.smtp_port)
                connection.ehlo()
                if self.config.smtp_use_tls and not self.config.smtp_use_ssl:
                    connection.starttls(context=ssl.create_default_context())
                    connection.ehlo()
                if self.config.smtp_username:
                    connection.login(
                        self.config.smtp_username, self.config.smtp_password or ""
                    )
                connection.send_message(message)
            finally:
                try:
                    connection.quit()
                except Exception:
                    # The message was already handed off (or already failed);
                    # a broken QUIT must not mask the real outcome.
                    try:
                        connection.close()
                    except Exception:
                        pass

            return ProviderOutcome(
                success=True, message_id=f"<smtp-{request.to}>"
            )
        except smtplib.SMTPRecipientsRefused as exc:
            return ProviderOutcome(
                success=False,
                error_code=SendErrorCode.INVALID_RECIPIENT,
                error_message=_recipient_refusal_text(exc),
                provider_code=str(getattr(exc, "smtp_code", "550")),
            )
        except smtplib.SMTPSenderRefused as exc:
            return ProviderOutcome(
                success=False,
                error_code=SendErrorCode.CONFIG_MISSING,
                error_message=f"Sender address refused: {exc}",
                provider_code=str(getattr(exc, "smtp_code", "550")),
            )
        except smtplib.SMTPAuthenticationError as exc:
            return ProviderOutcome(
                success=False,
                error_code=SendErrorCode.AUTH_FAILED,
                error_message=f"SMTP authentication failed: {exc}",
                provider_code=str(getattr(exc, "smtp_code", "535")),
            )
        except smtplib.SMTPNotSupportedError as exc:
            return ProviderOutcome(
                success=False,
                error_code=SendErrorCode.CONFIG_MISSING,
                error_message=f"SMTP feature not supported by server: {exc}",
                provider_code="smtp_not_supported",
            )
        except (
            smtplib.SMTPConnectError,
            smtplib.SMTPServerDisconnected,
            smtplib.SMTPException,
        ) as exc:
            return ProviderOutcome(
                success=False,
                error_code=SendErrorCode.PROVIDER_UNAVAILABLE,
                error_message=f"{type(exc).__name__}: {exc}",
                provider_code=type(exc).__name__,
            )
        except (socket.timeout, TimeoutError) as exc:
            return ProviderOutcome(
                success=False,
                error_code=SendErrorCode.NETWORK_ERROR,
                error_message=f"Timed out talking to the SMTP server: {exc}",
                provider_code="timeout",
            )
        except (OSError, ssl.SSLError) as exc:
            return ProviderOutcome(
                success=False,
                error_code=SendErrorCode.NETWORK_ERROR,
                error_message=f"{type(exc).__name__}: {exc}",
                provider_code=type(exc).__name__,
            )
        except Exception as exc:  # pragma: no cover - defensive
            return self._guard(exc)


def _recipient_refusal_text(exc: smtplib.SMTPRecipientsRefused) -> str:
    """Extract a readable message from ``SMTPRecipientsRefused``."""
    try:
        details = ", ".join(
            f"{addr}: {info[1].decode(errors='replace') if isinstance(info[1], bytes) else info[1]}"
            for addr, info in exc.recipients.items()
        )
    except Exception:  # pragma: no cover - defensive
        details = str(exc)
    return f"Recipient refused by server ({details})"


def build_default_providers(
    config: MessagingConfig,
    *,
    transport: Optional[HttpTransport] = None,
    smtp_connection_factory: Optional[Callable[[], Any]] = None,
) -> Dict[str, MessageProvider]:
    """
    Instantiate the provider set for a configuration.

    Args:
        config: Messaging configuration.
        transport: Shared HTTP transport for the WhatsApp provider.
        smtp_connection_factory: Injectable SMTP connection builder.

    Returns:
        Mapping of channel name to provider. ``whatsapp`` is omitted when
        ``whatsapp_provider`` is ``"none"``.
    """
    providers: Dict[str, MessageProvider] = {}

    if config.whatsapp_provider == "twilio":
        providers["whatsapp"] = TwilioProvider(
            config, channel="whatsapp", transport=transport
        )
    elif config.whatsapp_provider == "none":
        pass
    else:
        providers["whatsapp"] = MetaWhatsAppProvider(config, transport=transport)

    providers["sms"] = TwilioProvider(config, channel="sms", transport=transport)
    providers["email"] = SmtpEmailProvider(
        config, connection_factory=smtp_connection_factory
    )
    return providers
