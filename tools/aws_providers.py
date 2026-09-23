"""
AWS-backed outbound channels.

Two :class:`~tools.providers.MessageProvider` implementations:

* :class:`AwsPinpointSmsProvider` -- SMS via AWS End User Messaging SMS
  (the ``pinpoint-sms-voice-v2`` API).
* :class:`AwsSesEmailProvider` -- email via Amazon SES.

They follow the same contract as every other provider in this project: a
``send()`` call performs **exactly one** attempt and always returns a
:class:`~tools.providers.ProviderOutcome`. Nothing raises. A ``ClientError``
such as ``SubscriptionRequiredException`` ("the account is not onboarded") or
the SES sandbox's ``MessageRejected`` ("email address is not verified") becomes
a normalized :class:`~tools.errors.SendErrorCode` that the LLM can act on.

``boto3`` is imported **lazily**. It is an optional dependency: without it the
tools still load, the offline demo and the whole test suite run, and a live
send reports ``config_missing`` instead of exploding at import time. Both
providers accept an injected client so tests never touch AWS and never need
credentials.
"""

from __future__ import annotations
from typing import Any, Dict, Optional
import time

from tools.config import MessagingConfig
from tools.errors import SendErrorCode, from_boto_error
from tools.providers import MessageProvider, ProviderOutcome, SendRequest

#: MessageType for time-sensitive, non-promotional traffic. SMS reminders about
#: a patient's appointment are transactional by definition.
MESSAGE_TYPE_TRANSACTIONAL = "TRANSACTIONAL"


def load_boto3() -> Any:
    """
    Import ``boto3`` on demand.

    Returns:
        The ``boto3`` module.

    Raises:
        ImportError: When ``boto3`` is not installed.
    """
    import boto3

    return boto3


def _error_code_of(exc: BaseException) -> Optional[str]:
    """
    Extract an AWS error code from an exception.

    Handles both ``ClientError`` (which carries ``response["Error"]["Code"]``)
    and the botocore exceptions that have no structured response (timeouts,
    connection failures, missing credentials), for which the class name is the
    most useful identifier available.

    Args:
        exc: The exception raised by boto3/botocore.

    Returns:
        The raw error code, or ``None`` when the exception does not look like
        an AWS SDK error at all.
    """
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        error = response.get("Error")
        if isinstance(error, dict) and error.get("Code"):
            return str(error["Code"])
        if response.get("Code"):
            return str(response["Code"])

    module = type(exc).__module__ or ""
    if module.startswith("botocore") or module.startswith("boto3"):
        return type(exc).__name__
    return None


def _error_message_of(exc: BaseException) -> Optional[str]:
    """
    Extract the provider's own human-readable message from an exception.

    Args:
        exc: The exception raised by boto3/botocore.

    Returns:
        The message, or ``None`` when there is no structured error.
    """
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        error = response.get("Error")
        if isinstance(error, dict) and error.get("Message"):
            return str(error["Message"])
    return None


class _AwsProvider(MessageProvider):
    """
    Shared behaviour for the AWS-backed channels.

    Args:
        config: Messaging configuration (region, SES sender, dry-run policy).
        client: Pre-built boto3 client. Injected by tests; when omitted the
            client is created lazily with a default boto3 session.
        client_factory: Optional callable returning a client, used instead of
            constructing one with boto3. Lets callers supply their own session.
    """

    #: boto3 service name.
    service: str = "unknown"

    def __init__(
        self,
        config: MessagingConfig,
        *,
        client: Optional[Any] = None,
        client_factory: Optional[Any] = None,
    ) -> None:
        self.config = config
        self._client = client
        self._client_factory = client_factory
        #: Why the client is unavailable, for the ``config_missing`` message.
        self.error: Optional[str] = None

    def _resolve_client(self) -> Optional[Any]:
        """
        Return a usable client, creating it once and caching the result.

        Returns:
            The client, or ``None`` when it cannot be built (the reason is
            stored in :attr:`error`).
        """
        if self._client is not None:
            return self._client
        if self.error:
            return None

        if self._client_factory is not None:
            try:
                self._client = self._client_factory()
                return self._client
            except Exception as exc:  # pragma: no cover - defensive
                self.error = (
                    f"could not create the {self.service} client: "
                    f"{type(exc).__name__}: {exc}"
                )
                return None

        try:
            boto3 = load_boto3()
        except ImportError:
            self.error = (
                "boto3 is not installed; install it to send over AWS "
                "(pip install boto3)"
            )
            return None

        try:
            self._client = boto3.client(
                self.service, region_name=self.config.aws_region
            )
        except Exception as exc:  # pragma: no cover - defensive
            self.error = (
                f"could not create the {self.service} client in region "
                f"'{self.config.aws_region}': {type(exc).__name__}: {exc}"
            )
            return None
        return self._client

    def is_configured(self) -> bool:
        """
        Whether this channel can attempt a real send.

        Only checks that a client can be obtained; whether the account is
        entitled to the service is discovered by the API call itself and
        reported as ``not_subscribed``.

        Returns:
            True when a client is available.
        """
        return self._resolve_client() is not None

    def _dispatch(self, client: Any, request: SendRequest) -> Dict[str, Any]:
        """
        Perform the single API call for this channel.

        Args:
            client: The boto3 client.
            request: The message to deliver.

        Returns:
            The raw API response.
        """
        raise NotImplementedError  # pragma: no cover - abstract

    def _classify_exception(self, exc: BaseException) -> ProviderOutcome:
        """
        Turn a boto3/botocore exception into a normalized failure.

        Args:
            exc: The exception raised during the API call.

        Returns:
            A failed :class:`ProviderOutcome`, or the generic guard outcome
            when the exception is not AWS-shaped (i.e. a bug of ours).
        """
        code = _error_code_of(exc)
        if code is None:
            return self._guard(exc)
        message = _error_message_of(exc) or f"{type(exc).__name__}: {exc}"
        return ProviderOutcome(
            success=False,
            error_code=from_boto_error(code),
            error_message=message,
            provider_code=code,
        )

    def send(self, request: SendRequest) -> ProviderOutcome:
        """
        Deliver the message, once, reporting every failure as data.

        In dry-run mode (the default) nothing is transmitted and the outcome is
        marked ``simulated``; no client is created and no network call is made.

        Args:
            request: The message to deliver.

        Returns:
            A :class:`ProviderOutcome`; never an exception.
        """
        started = time.monotonic()

        try:
            if not self.config.live_requested:
                return ProviderOutcome(success=True, simulated=True)

            client = self._resolve_client()
            if client is None:
                return ProviderOutcome(
                    success=False,
                    error_code=SendErrorCode.CONFIG_MISSING,
                    error_message=self.error or f"{self.service} is not configured",
                    provider_code="config_missing",
                )

            try:
                response = self._dispatch(client, request)
            except Exception as exc:
                outcome = self._classify_exception(exc)
                outcome.latency_ms = self._elapsed(started)
                return outcome

            message_id: Optional[str] = None
            if isinstance(response, dict):
                raw_id = response.get("MessageId") or response.get("messageId")
                if raw_id:
                    message_id = str(raw_id)

            return ProviderOutcome(
                success=True,
                message_id=message_id,
                latency_ms=self._elapsed(started),
                raw=response if isinstance(response, dict) else None,
            )
        except Exception as exc:  # pragma: no cover - defensive
            return self._guard(exc)

    @staticmethod
    def _elapsed(started: float) -> int:
        """Milliseconds elapsed since ``started``."""
        return int((time.monotonic() - started) * 1000)


class AwsPinpointSmsProvider(_AwsProvider):
    """
    SMS via AWS End User Messaging SMS (``pinpoint-sms-voice-v2``).

    Sends with ``MessageType="TRANSACTIONAL"``, which is the correct type for
    an appointment reminder. ``OriginationIdentity`` is only included when
    configured, because the API treats it as optional.
    """

    channel = "sms"
    service = "pinpoint-sms-voice-v2"

    def _dispatch(self, client: Any, request: SendRequest) -> Dict[str, Any]:
        """
        Call ``SendTextMessage``.

        Args:
            client: The ``pinpoint-sms-voice-v2`` client.
            request: The message to deliver (``to`` is E.164, ``body`` the text).

        Returns:
            The API response, which carries ``MessageId``.
        """
        kwargs: Dict[str, Any] = {
            "DestinationPhoneNumber": request.to,
            "MessageBody": request.body or "",
            "MessageType": MESSAGE_TYPE_TRANSACTIONAL,
        }
        if self.config.aws_sms_origination_identity:
            kwargs["OriginationIdentity"] = self.config.aws_sms_origination_identity
        return client.send_text_message(**kwargs)


class AwsSesEmailProvider(_AwsProvider):
    """
    Email via Amazon SES.

    The ``Source`` is the verified identity from configuration; while the
    account is in the SES sandbox only verified recipients are deliverable,
    which is exactly what the ``send_email`` tool's allow-list enforces.
    """

    channel = "email"
    service = "ses"

    def _dispatch(self, client: Any, request: SendRequest) -> Dict[str, Any]:
        """
        Call ``SendEmail`` with a plain-text body.

        Args:
            client: The ``ses`` client.
            request: The message to deliver.

        Returns:
            The API response, which carries ``MessageId``.
        """
        return client.send_email(
            Source=self.config.aws_ses_source,
            Destination={"ToAddresses": [request.to]},
            Message={
                "Subject": {"Data": request.subject or ""},
                "Body": {"Text": {"Data": request.body or ""}},
            },
        )
