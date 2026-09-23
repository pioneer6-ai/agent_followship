"""
The single return type of every messaging tool.

A send tool must never raise: the LLM has to be able to *observe* failure and
choose the next move (different channel, retry later, escalate to staff).
So every tool call returns a :class:`ToolResult`, which is JSON-serializable
and self-describing enough for a model to act on.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
import json

from tools.errors import (
    SendErrorCode,
    hint_for,
    is_retryable,
    suggested_fallbacks,
)


@dataclass
class ToolResult:
    """
    Outcome of a single deterministic send attempt.

    Attributes:
        tool: Name of the tool that was invoked (e.g. ``send_whatsapp_message``).
        success: True only when the provider accepted the message (or the send
            was simulated in dry-run mode).
        channel: Logical channel: ``whatsapp``, ``sms`` or ``email``.
        recipient: Normalized destination the message was addressed to.
        message_id: Provider-assigned id (WhatsApp message id / Twilio SID).
        simulated: True when nothing was actually transmitted (dry-run).
        kind: What the tool did: ``"send"`` (delivery attempt), ``"info"``
            (decision support) or ``"handoff"`` (human escalation). Drives the
            ``status`` wording the model sees.
        data: Optional tool-specific payload for non-send tools (candidate
            channels, template catalogue, escalation receipt).
        error_code: Normalized failure code, when ``success`` is False.
        error_message: Provider-supplied human-readable message.
        provider_code: Raw provider error code/string, always preserved.
        retryable: Whether retrying *this* channel later may succeed.
        suggested_fallback_channels: Channels worth trying instead.
        hint: Actionable guidance written for an LLM consumer.
        latency_ms: Round-trip time of the provider call.
    """

    tool: str
    success: bool
    channel: str
    recipient: str
    message_id: Optional[str] = None
    simulated: bool = False
    kind: str = "send"
    data: Optional[Dict[str, Any]] = None
    error_code: Optional[SendErrorCode] = None
    error_message: Optional[str] = None
    provider_code: Optional[str] = None
    retryable: bool = False
    suggested_fallback_channels: List[str] = field(default_factory=list)
    hint: Optional[str] = None
    latency_ms: int = 0

    def __post_init__(self) -> None:
        """Derive retry/fallback guidance so callers never have to."""
        if self.success:
            # A successful send carries no failure metadata.
            self.error_code = None
            self.error_message = None
            self.retryable = False
            self.suggested_fallback_channels = []
            return

        if self.error_code is None:
            self.error_code = SendErrorCode.UNKNOWN

        if not self.retryable:
            self.retryable = is_retryable(self.error_code)
        if not self.suggested_fallback_channels:
            # Never suggest the channel that just failed: retrying it is
            # exactly what the model must not do.
            self.suggested_fallback_channels = [
                name
                for name in suggested_fallbacks(self.error_code)
                if name != self.channel
            ]
        if not self.hint:
            self.hint = hint_for(self.error_code)

    @property
    def is_error(self) -> bool:
        """Anthropic ``tool_result.is_error`` flag."""
        return not self.success

    @property
    def status(self) -> str:
        """
        The word the model sees in the payload.

        Returns:
            ``"sent"``, ``"ok"``, ``"escalated"`` on success (depending on
            :attr:`kind`), otherwise ``"failed"``.
        """
        if not self.success:
            return "failed"
        return {"send": "sent", "info": "ok", "handoff": "escalated"}.get(
            self.kind, "sent"
        )

    def to_payload(self) -> Dict[str, Any]:
        """
        Build the compact JSON object handed back to the LLM as tool output.

        ``None`` values are dropped so the model sees a tight, signal-only
        object rather than a wall of nulls.
        """
        payload: Dict[str, Any] = {
            "status": self.status,
            "channel": self.channel,
            "recipient": self.recipient,
        }

        if self.data:
            payload["data"] = self.data

        if self.success:
            if self.message_id:
                payload["message_id"] = self.message_id
            if self.simulated:
                payload["simulated"] = True
                payload["note"] = (
                    "Dry-run: no real message was transmitted. Report this as a "
                    "simulated send, not a delivered message."
                )
            return payload

        payload["error_code"] = self.error_code.value if self.error_code else "unknown"
        if self.error_message:
            payload["message"] = self.error_message
        if self.provider_code:
            payload["provider_code"] = self.provider_code
        payload["retryable"] = self.retryable
        payload["suggested_fallback_channels"] = list(self.suggested_fallback_channels)
        if self.hint:
            payload["hint"] = self.hint
        return payload

    def to_json(self) -> str:
        """Serialize :meth:`to_payload` for use as ``tool_result.content``."""
        return json.dumps(self.to_payload(), ensure_ascii=False)

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        if self.success:
            suffix = " (simulated)" if self.simulated else ""
            return f"{self.tool} -> {self.status} via {self.channel}{suffix}"
        code = self.error_code.value if self.error_code else "unknown"
        return f"{self.tool} -> FAILED via {self.channel} [{code}]"
