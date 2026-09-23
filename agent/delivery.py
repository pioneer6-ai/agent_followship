"""
Structured delivery outcomes and delivery backends for the notification layer.

The agent's contract with the outside world used to be a bare ``bool``: a channel
either "sent" or it did not. That is not enough information for an agent. When a
send fails it needs to know *why* (``error_code``), whether trying again could
help (``retryable``), and what to try instead (``suggested_fallbacks``) so it can
choose a different channel rather than crash or, worse, report success.

This module supplies that vocabulary and two interchangeable backends:

``PrintDeliveryBackend``
    The offline default. Prints what *would* be sent and reports success. Used by
    the demo and the test suite, which have no credentials and must never touch a
    live provider.

``ToolkitDeliveryBackend``
    The real path. Delegates to the deterministic send functions in
    :mod:`tools.messaging` (Twilio / WhatsApp / SMTP), which already implement
    address normalization, allow-lists, dry-run and a complete error taxonomy.
    Provider failures come back as :class:`NotificationOutcome` values carrying
    the normalized ``error_code`` and fallback hints -- they are never raised.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.models import ContactChannel

#: Tool that performs the send for each channel. ``PHONE_CALL`` is absent on
#: purpose: nothing in :mod:`tools.messaging` places a voice call, so the agent
#: must fall back to a channel it can actually use.
TOOL_FOR_CHANNEL: Dict[ContactChannel, str] = {
    ContactChannel.WHATSAPP: "send_whatsapp_message",
    ContactChannel.SMS: "send_sms_message",
    ContactChannel.EMAIL: "send_email_message",
}

#: Provider channel names (Twilio/Meta use ``"sms"``/``"whatsapp"``) to enum, so
#: a fallback hint coming back from the tool layer maps onto the domain enum.
_TOOL_CHANNEL_NAMES: Dict[str, ContactChannel] = {
    "sms": ContactChannel.SMS,
    "whatsapp": ContactChannel.WHATSAPP,
    "email": ContactChannel.EMAIL,
    "phone_call": ContactChannel.PHONE_CALL,
}


@dataclass
class NotificationOutcome:
    """
    Result of one delivery attempt, in a form the agent can reason about.

    Mirrors the fields of :class:`tools.result.ToolResult` that matter for
    decision-making, so nothing is lost in translation between the tool layer and
    the agent layer.

    Attributes:
        success: True only when the provider accepted the message.
        channel: Channel the attempt was made on.
        recipient: Address the message was addressed to, after normalization.
        message_id: Provider-assigned id, when the send succeeded.
        error_code: Normalized failure code (e.g. ``recipient_not_verified``),
            or ``None`` when the send succeeded.
        error_message: Human-readable failure text, when available.
        provider_code: Raw provider error code, preserved verbatim.
        retryable: Whether retrying *this* channel later could succeed.
        suggested_fallbacks: Channels worth trying instead, when this one failed.
        hint: Actionable guidance written for a language model.
        simulated: True when nothing was actually transmitted (dry-run).
    """

    success: bool
    channel: ContactChannel
    recipient: str = ""
    message_id: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    provider_code: Optional[str] = None
    retryable: bool = False
    suggested_fallbacks: List[ContactChannel] = field(default_factory=list)
    hint: Optional[str] = None
    simulated: bool = False

    @property
    def failed(self) -> bool:
        """Whether the attempt did not get the message out."""
        return not self.success

    def describe(self) -> str:
        """
        Render a single line suitable for a conversation log or an audit entry.
        """
        if self.success:
            suffix = " (simulated)" if self.simulated else ""
            return f"{self.channel.value} -> {self.recipient}: sent{suffix}"
        return (
            f"{self.channel.value} -> {self.recipient}: failed "
            f"[{self.error_code or 'unknown'}] {self.error_message or ''}".rstrip()
        )


class DeliveryBackend(ABC):
    """Transmits (or simulates) one message on one channel."""

    @abstractmethod
    def deliver(
        self,
        channel: ContactChannel,
        recipient: str,
        body: str,
        subject: str = "",
    ) -> NotificationOutcome:
        """
        Attempt one delivery.

        Implementations must never raise: a failure is an outcome the agent has
        to perceive, not an exception that ends the cycle.

        Args:
            channel: Channel to send on.
            recipient: Destination address or number.
            body: Message text.
            subject: Subject line, used by the email channel.

        Returns:
            The outcome of the attempt.
        """
        raise NotImplementedError

    @property
    def name(self) -> str:
        """Short identifier for logs and audit entries."""
        return type(self).__name__


def missing_recipient_outcome(channel: ContactChannel) -> NotificationOutcome:
    """Outcome for "the patient has no address on this channel"."""
    return NotificationOutcome(
        success=False,
        channel=channel,
        error_code="missing_recipient",
        error_message=f"patient has no contact detail for {channel.value}",
        hint=(
            f"No {channel.value} address on file for this patient. Try another "
            "channel the patient has provided, or escalate if none remain."
        ),
        suggested_fallbacks=_other_channels(channel),
    )


def _empty_body(channel: ContactChannel) -> NotificationOutcome:
    """Outcome for an attempt with nothing to say."""
    return NotificationOutcome(
        success=False,
        channel=channel,
        error_code="invalid_request",
        error_message="message body is empty",
        hint="Compose a non-empty message before sending.",
    )


def _other_channels(channel: ContactChannel) -> List[ContactChannel]:
    """Every channel except ``channel`` that can actually transmit a message."""
    return [c for c in TOOL_FOR_CHANNEL if c is not channel]


class PrintDeliveryBackend(DeliveryBackend):
    """
    Offline backend that prints instead of transmitting, and always succeeds.

    This is the default so that the demo and the test suite exercise the whole
    agent loop without credentials, network access, or any risk of contacting a
    real patient.
    """

    def deliver(
        self,
        channel: ContactChannel,
        recipient: str,
        body: str,
        subject: str = "",
    ) -> NotificationOutcome:
        """Print the message and report success."""
        if not recipient:
            return missing_recipient_outcome(channel)
        if not body:
            return _empty_body(channel)

        label = subject or "message"
        print(f"[{channel.value}] Sending to {recipient}:")
        print(f"       Subject: {label}")
        for line in body.splitlines() or [""]:
            print(f"       {line}")

        return NotificationOutcome(
            success=True,
            channel=channel,
            recipient=recipient,
            message_id=f"simulated-{channel.value}-{abs(hash((recipient, body))) % 10**8}",
            simulated=True,
        )


class ToolkitDeliveryBackend(DeliveryBackend):
    """
    Real backend: routes each channel through the deterministic tool functions.

    Those functions already enforce the guard order normalize -> non-empty body ->
    allow-list -> dry-run -> provider, and normalize every provider error into a
    :class:`~tools.result.ToolResult`. This class only translates that result into
    the agent's own outcome type, so the agent layer never has to know about
    boto3, Twilio or SMTP.
    """

    def __init__(self, registry: Any) -> None:
        """
        Args:
            registry: A :class:`tools.messaging.ToolRegistry` (or any object with a
                compatible ``call(name, arguments)`` method).
        """
        self.registry = registry

    @classmethod
    def from_environment(cls) -> "ToolkitDeliveryBackend":
        """
        Build a backend from environment configuration (loading ``.env`` first
        when one is present).

        Returns:
            A backend wired to the providers the environment configures. When
            nothing is configured the providers resolve to their dry-run behaviour,
            so this never sends unless ``MESSAGING_DRY_RUN=0`` is set explicitly.
        """
        from tools.config import MessagingConfig, load_env_file
        from tools.messaging import build_tool_registry

        load_env_file()
        config = MessagingConfig.from_env()
        return cls(build_tool_registry(config=config))

    def deliver(
        self,
        channel: ContactChannel,
        recipient: str,
        body: str,
        subject: str = "",
    ) -> NotificationOutcome:
        """
        Send once through the corresponding tool.

        Never raises: the underlying tool already returns structured failures, and
        anything unexpected is converted into an outcome as well.
        """
        if not recipient:
            return missing_recipient_outcome(channel)
        if not body:
            return _empty_body(channel)

        tool_name = TOOL_FOR_CHANNEL.get(channel)
        if tool_name is None:
            return NotificationOutcome(
                success=False,
                channel=channel,
                recipient=recipient,
                error_code="unsupported_channel",
                error_message=f"no tool transmits on {channel.value}",
                retryable=False,
                hint=(
                    f"No automated sender exists for {channel.value}. Use a channel "
                    "the patient has a reachable address on, or escalate to staff."
                ),
                suggested_fallbacks=_other_channels(channel),
            )

        arguments: Dict[str, Any] = {"recipient": recipient, "body": body}
        if channel is ContactChannel.EMAIL and subject:
            arguments["subject"] = subject

        try:
            result = self.registry.call(tool_name, arguments)
        except Exception as exc:  # pragma: no cover - defensive boundary
            return NotificationOutcome(
                success=False,
                channel=channel,
                recipient=recipient,
                error_code="unexpected",
                error_message=f"{type(exc).__name__}: {exc}",
                retryable=True,
                hint="Unexpected failure; retry once before escalating.",
                suggested_fallbacks=_other_channels(channel),
            )

        return self._translate(result, channel, recipient)

    @staticmethod
    def _translate(
        result: Any, channel: ContactChannel, recipient: str
    ) -> NotificationOutcome:
        """Convert a ``ToolResult`` (or a plain mapping) into an outcome."""
        if isinstance(result, dict):
            get = result.get
        else:
            get = lambda key, default=None: getattr(result, key, default)  # noqa: E731

        raw_fallbacks = get("suggested_fallback_channels") or []
        fallbacks: List[ContactChannel] = []
        for item in raw_fallbacks:
            named = getattr(item, "value", item)
            mapped = _TOOL_CHANNEL_NAMES.get(str(named))
            if mapped is not None and mapped not in fallbacks:
                fallbacks.append(mapped)

        error_code = get("error_code")
        if error_code is not None:
            error_code = str(getattr(error_code, "value", error_code))

        return NotificationOutcome(
            success=bool(get("success")),
            channel=channel,
            recipient=get("recipient") or recipient,
            message_id=get("message_id"),
            error_code=error_code,
            error_message=get("error_message"),
            provider_code=get("provider_code"),
            retryable=bool(get("retryable")),
            suggested_fallbacks=fallbacks,
            hint=get("hint"),
            simulated=bool(get("simulated")),
        )


def is_configured_for_live_sends() -> bool:
    """
    Whether the agent may transmit for real.

    Two independent switches must both be thrown, because the failure mode of
    getting this wrong is texting a patient:

    1. ``MESSAGING_DRY_RUN=0`` in the tool layer's configuration, and
    2. ``AGENT_LIVE_SENDS=1`` in the agent's own environment.

    The agent therefore stays offline even when a fully populated ``.env`` is
    present, which is what makes running the demo safe. Requiring the second
    switch is deliberate: the tool layer's dry-run flag is about the tool layer,
    and the agent should not inherit "live" from a file it did not ask for.

    Returns:
        True only when both switches are set.
    """
    from tools.config import MessagingConfig, load_env_file

    load_env_file()
    if not _env_flag("AGENT_LIVE_SENDS"):
        return False
    return MessagingConfig.from_env().live_requested


def _env_flag(name: str) -> bool:
    """Read a boolean environment variable (``1/true/yes/on``)."""
    import os

    raw = os.environ.get(name)
    if raw is None:
        return False
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}
