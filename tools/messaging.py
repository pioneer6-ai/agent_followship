"""
The deterministic messaging tools the LLM is allowed to call.

This module is the boundary between "the model decided something" and "a real
HTTP request left the building". Every function here:

* validates its arguments,
* performs **exactly one** delivery attempt,
* returns a :class:`~tools.result.ToolResult`,
* and **never raises** -- not for a bad phone number, not for a missing API
  key, not for "recipient not verified" coming back from WhatsApp.

That last point is the whole reason this layer exists. A rejected send is
information, not a crash: the model receives ``status: "failed"`` together
with a normalized ``error_code``, a ``retryable`` flag and
``suggested_fallback_channels``, so it can switch channel or escalate on the
next turn instead of the program dying.

Exposed tools
-------------

===========================  ===============================================
Tool                         Purpose
===========================  ===============================================
``send_whatsapp_message``    WhatsApp via Meta Cloud API or Twilio
``send_sms_message``         SMS via Twilio
``send_email_message``       Email via SMTP
``get_candidate_send_channels``  Ranked, consent-aware channel options
``list_message_templates``   The approved template catalogue
``escalate_to_staff``        Hand the case to a human
===========================  ===============================================
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence
import inspect
import json
import re

from tools.config import MessagingConfig, TemplateSpec
from tools.errors import SendErrorCode, suggested_fallbacks
from tools.providers import (
    MessageProvider,
    SendRequest,
    build_default_providers,
)
from tools.result import ToolResult
from tools.transport import HttpTransport

#: All channel names understood by the toolkit, in default preference order.
CHANNEL_ORDER: Sequence[str] = ("whatsapp", "sms", "email")

#: Public tool names, used by :mod:`tools.schemas` to keep a single source of truth.
TOOL_NAMES: Sequence[str] = (
    "send_whatsapp_message",
    "send_sms_message",
    "send_email_message",
    "get_candidate_send_channels",
    "list_message_templates",
    "escalate_to_staff",
)

_WHATSAPP_PREFIX = "whatsapp:"
_PHONE_JUNK = re.compile(r"[\s\-().]")
_E164 = re.compile(r"\+?\d{7,15}")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$")
_MAX_BODY_CHARS = 4096


def normalize_phone(
    raw: Any, default_country_code: Optional[str] = None
) -> Optional[str]:
    """
    Normalize a phone number to E.164-ish form.

    Accepts ``+1 (555) 000-1111``, ``whatsapp:+15550001111``, ``0015550001111``
    and bare national numbers (when a default country code is configured).

    Args:
        raw: The value supplied by the model.
        default_country_code: Prefix such as ``"+86"`` applied when the value
            carries no country code.

    Returns:
        ``"+<digits>"``, or ``None`` when the value cannot be a phone number.
    """
    if raw is None:
        return None
    value = str(raw).strip()
    if not value:
        return None
    if value.lower().startswith(_WHATSAPP_PREFIX):
        value = value[len(_WHATSAPP_PREFIX):]
    value = _PHONE_JUNK.sub("", value)
    if value.startswith("00"):
        value = "+" + value[2:]
    if not _E164.fullmatch(value):
        return None
    if not value.startswith("+"):
        if default_country_code:
            return f"{default_country_code}{value.lstrip('0')}"
        value = "+" + value
    return value


def normalize_email(raw: Any) -> Optional[str]:
    """
    Validate and normalize an email address.

    Args:
        raw: The value supplied by the model.

    Returns:
        The lower-cased address, or ``None`` when it is not a usable address.
    """
    if raw is None:
        return None
    value = str(raw).strip().strip("<>").strip()
    if not value or len(value) > 254:
        return None
    if not _EMAIL.match(value):
        return None
    return value.lower()


def _coerce_params(raw: Any) -> List[str]:
    """
    Coerce template parameters into a list of strings.

    Accepts a list/tuple, or a JSON-encoded string (models sometimes emit one).

    Returns:
        A list of strings; empty when the input is unusable.
    """
    if raw is None or raw == "":
        return []
    values: Any = raw
    if isinstance(raw, str):
        stripped = raw.strip()
        if stripped.startswith("["):
            try:
                values = json.loads(stripped)
            except ValueError:
                values = [raw]
        elif "," in stripped:
            values = [part.strip() for part in stripped.split(",")]
        else:
            values = [stripped]
    if isinstance(values, (list, tuple)):
        return [str(v) for v in values if v is not None]
    return [str(values)]


def _clip(text: Optional[str], limit: int = _MAX_BODY_CHARS) -> str:
    """Bound message length so a runaway model cannot send megabytes."""
    value = str(text or "")
    return value if len(value) <= limit else value[: limit - 1] + "\u2026"


@dataclass
class EscalationRecord:
    """
    A staff escalation, kept so failures have an auditable destination.

    Attributes:
        escalation_id: Generated identifier handed back to the model.
        case_id: Follow-up case the escalation belongs to.
        patient_id: Patient the case belongs to.
        reason: Why the agent gave up automating and asked for a human.
        details: Free-form context (last error, attempted channels, ...).
        urgency: ``"low"`` | ``"normal"`` | ``"high"``.
        created_at: ISO-8601 UTC timestamp.
    """

    escalation_id: str
    case_id: str
    patient_id: str
    reason: str
    details: Dict[str, Any] = field(default_factory=dict)
    urgency: str = "normal"
    created_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for logs and tool output."""
        return {
            "escalation_id": self.escalation_id,
            "case_id": self.case_id,
            "patient_id": self.patient_id,
            "reason": self.reason,
            "details": self.details,
            "urgency": self.urgency,
            "created_at": self.created_at,
        }


class EscalationLog:
    """
    In-memory escalation sink with optional JSONL persistence.

    The whole point is that "the agent could not send anything" is never a
    silent dead end: it becomes a durable record a human can act on.
    """

    def __init__(self, file_path: Optional[str] = None) -> None:
        """
        Args:
            file_path: Optional JSONL file appended to on every escalation.
        """
        self.file_path = file_path
        self.records: List[EscalationRecord] = []
        self._counter = 0

    def record(
        self,
        case_id: str,
        patient_id: str,
        reason: str,
        details: Optional[Mapping[str, Any]] = None,
        urgency: str = "normal",
    ) -> EscalationRecord:
        """
        Store one escalation.

        Args:
            case_id: Follow-up case id.
            patient_id: Patient id.
            reason: Short explanation of the hand-off.
            details: Extra structured context.
            urgency: Priority hint for staff.

        Returns:
            The stored :class:`EscalationRecord`.
        """
        self._counter += 1
        created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        record = EscalationRecord(
            escalation_id=f"ESC-{created_at[:10].replace('-', '')}-{self._counter:04d}",
            case_id=str(case_id),
            patient_id=str(patient_id),
            reason=str(reason),
            details=dict(details or {}),
            urgency=str(urgency),
            created_at=created_at,
        )
        self.records.append(record)
        if self.file_path:
            try:
                with open(self.file_path, "a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")
            except OSError:
                # Losing the file copy must not lose the escalation itself.
                pass
        return record


class MessagingToolkit:
    """
    Deterministic implementation behind every tool name.

    Args:
        config: Messaging configuration (providers, templates, dry-run policy).
        providers: Channel name -> provider. Defaults to the standard set.
        escalation_log: Sink for ``escalate_to_staff``.
    """

    def __init__(
        self,
        config: Optional[MessagingConfig] = None,
        *,
        providers: Optional[Dict[str, MessageProvider]] = None,
        transport: Optional[HttpTransport] = None,
        smtp_connection_factory: Optional[Callable[[], Any]] = None,
        escalation_log: Optional[EscalationLog] = None,
    ) -> None:
        self.config = config or MessagingConfig.from_env()
        self.providers: Dict[str, MessageProvider] = providers or build_default_providers(
            self.config,
            transport=transport,
            smtp_connection_factory=smtp_connection_factory,
        )
        self.escalation_log = escalation_log or EscalationLog()
        self.history: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Tool: send via the three supported channels
    # ------------------------------------------------------------------

    def send_whatsapp_message(
        self,
        recipient: Any,
        template: Optional[str] = None,
        params: Any = None,
        body: Optional[str] = None,
        language: Optional[str] = None,
    ) -> ToolResult:
        """
        Send a WhatsApp message, once.

        Args:
            recipient: Phone number (E.164 preferred).
            template: Approved template name. Required for business-initiated
                sends outside the 24-hour customer service window.
            params: Positional template parameters, in template order.
            body: Free-form text, used instead of a template when in-window.
            language: Template language code (defaults to configuration).

        Returns:
            A :class:`ToolResult`: ``success=True`` when WhatsApp accepted the
            message, otherwise a normalized failure such as
            ``recipient_not_verified`` with fallback suggestions.
        """
        return self._send(
            tool="send_whatsapp_message",
            channel="whatsapp",
            recipient_raw=recipient,
            normalize=normalize_phone,
            template=template,
            params=params,
            body=body,
            language=language,
        )

    def send_sms_message(
        self,
        recipient: Any,
        body: Optional[str] = None,
        template: Optional[str] = None,
        params: Any = None,
    ) -> ToolResult:
        """
        Send an SMS message, once.

        Args:
            recipient: Phone number (E.164 preferred).
            body: Message text.
            template: Optional template whose rendered text is used when
                ``body`` is omitted (SMS has no server-side templates).
            params: Positional template parameters.

        Returns:
            A :class:`ToolResult`.
        """
        return self._send(
            tool="send_sms_message",
            channel="sms",
            recipient_raw=recipient,
            normalize=normalize_phone,
            template=template,
            params=params,
            body=body,
        )

    def send_email_message(
        self,
        recipient: Any,
        subject: Optional[str] = None,
        body: Optional[str] = None,
        template: Optional[str] = None,
        params: Any = None,
    ) -> ToolResult:
        """
        Send an email, once.

        Args:
            recipient: Email address.
            subject: Subject line.
            body: Plain-text body; rendered from ``template`` when omitted.
            template: Optional catalogue template name.
            params: Positional template parameters.

        Returns:
            A :class:`ToolResult`.
        """
        return self._send(
            tool="send_email_message",
            channel="email",
            recipient_raw=recipient,
            normalize=normalize_email,
            template=template,
            params=params,
            body=body,
            subject=subject,
        )

    # ------------------------------------------------------------------
    # Tools: decision support
    # ------------------------------------------------------------------

    def get_candidate_send_channels(
        self,
        preferred_channel: Optional[str] = None,
        failed_channel: Optional[str] = None,
        error_code: Optional[str] = None,
    ) -> ToolResult:
        """
        Rank the channels worth trying, given what already failed and why.

        This is the tool the model calls after a failure: it turns
        ``recipient_not_verified`` on WhatsApp into "use sms, then email".

        Args:
            preferred_channel: The patient's preferred channel, if known.
            failed_channel: A channel that just failed and should be skipped.
            error_code: Normalized error code from that failure, used to pick
                the right fallback set.

        Returns:
            A successful :class:`ToolResult` whose ``data`` holds
            ``fallback_order`` (the recommended order) and per-channel
            ``channels`` details including configuration status.
        """
        try:
            code: Optional[SendErrorCode] = None
            if error_code:
                try:
                    code = SendErrorCode(str(error_code))
                except ValueError:
                    code = None

            failed = (failed_channel or "").strip().lower() or None

            order: List[str] = []
            if code is not None:
                order.extend(suggested_fallbacks(code))
            if preferred_channel:
                order.insert(0, preferred_channel.strip().lower())
            if not order and failed is None:
                order.extend(CHANNEL_ORDER)
            order.extend(CHANNEL_ORDER)

            deduped: List[str] = []
            for name in order:
                if name in CHANNEL_ORDER and name != failed and name not in deduped:
                    deduped.append(name)

            channels: Dict[str, Dict[str, Any]] = {}
            for name in CHANNEL_ORDER:
                provider = self.providers.get(name)
                configured = bool(provider and provider.is_configured())
                channels[name] = {
                    "available": provider is not None,
                    "configured": configured,
                    "simulated": bool(provider and self.config.simulates),
                    "just_failed": name == failed,
                    "preferred": name == (preferred_channel or "").strip().lower(),
                }

            payload = {
                "fallback_order": deduped,
                "channels": channels,
                "reason": (
                    f"After a '{code.value}' failure on '{failed}'"
                    if code is not None and failed
                    else "Default channel preference order"
                ),
                "guidance": (
                    "Attempt the channels in fallback_order, skipping any with "
                    "available=false. If fallback_order is empty, retry later for "
                    "retryable errors or call escalate_to_staff."
                ),
            }
            return ToolResult(
                tool="get_candidate_send_channels",
                success=True,
                channel="none",
                recipient="",
                kind="info",
                data=payload,
            )
        except Exception as exc:  # pragma: no cover - defensive
            return self._unexpected("get_candidate_send_channels", "none", exc, "info")

    def list_message_templates(self) -> ToolResult:
        """
        Return the approved template catalogue.

        Returns:
            A successful :class:`ToolResult` whose ``data`` lists each template
            with its parameter count and expected parameter names.
        """
        try:
            templates: List[Dict[str, Any]] = []
            for name in self.config.template_names():
                spec = self.config.templates[name]
                templates.append(
                    {
                        "name": spec.name,
                        "param_count": spec.param_count,
                        "language": spec.language,
                        "description": spec.description,
                    }
                )
            return ToolResult(
                tool="list_message_templates",
                success=True,
                channel="none",
                recipient="",
                kind="info",
                data={"templates": templates, "count": len(templates)},
            )
        except Exception as exc:  # pragma: no cover - defensive
            return self._unexpected("list_message_templates", "none", exc, "info")

    # ------------------------------------------------------------------
    # Tool: human hand-off
    # ------------------------------------------------------------------

    def escalate_to_staff(
        self,
        case_id: Any,
        patient_id: Any,
        reason: Any,
        details: Any = None,
        urgency: str = "normal",
    ) -> ToolResult:
        """
        Hand a case to a human because automation cannot proceed.

        Use this when every channel has failed, when consent/credentials are
        broken, or when the patient asks for a person.

        Args:
            case_id: Follow-up case identifier.
            patient_id: Patient identifier.
            reason: Why a human is needed.
            details: Optional structured context (last error, attempts).
            urgency: ``"low"``, ``"normal"`` or ``"high"``.

        Returns:
            A successful :class:`ToolResult` whose ``data`` carries the
            generated ``escalation_id``.
        """
        try:
            if not str(case_id or "").strip() or not str(patient_id or "").strip():
                return ToolResult(
                    tool="escalate_to_staff",
                    success=False,
                    channel="staff",
                    recipient="",
                    error_code=SendErrorCode.INVALID_REQUEST,
                    error_message="case_id and patient_id are both required",
                    provider_code="validation",
                )
            if not str(reason or "").strip():
                return ToolResult(
                    tool="escalate_to_staff",
                    success=False,
                    channel="staff",
                    recipient="",
                    error_code=SendErrorCode.INVALID_REQUEST,
                    error_message="reason is required so staff know what to do",
                    provider_code="validation",
                )

            detail_payload: Dict[str, Any] = {}
            if isinstance(details, Mapping):
                detail_payload = {str(k): v for k, v in details.items()}
            elif details:
                detail_payload = {"note": str(details)}

            normalized_urgency = str(urgency or "normal").strip().lower()
            if normalized_urgency not in {"low", "normal", "high"}:
                normalized_urgency = "normal"

            record = self.escalation_log.record(
                case_id=case_id,
                patient_id=patient_id,
                reason=reason,
                details=detail_payload,
                urgency=normalized_urgency,
            )
            result = ToolResult(
                tool="escalate_to_staff",
                success=True,
                channel="staff",
                recipient=str(patient_id),
                message_id=record.escalation_id,
                kind="handoff",
                data={
                    "escalation_id": record.escalation_id,
                    "case_id": record.case_id,
                    "urgency": record.urgency,
                    "note": "Recorded for human review. Do not retry automated sends.",
                },
            )
            self._record(result)
            return result
        except Exception as exc:  # pragma: no cover - defensive
            return self._unexpected("escalate_to_staff", "staff", exc, "handoff")

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _send(
        self,
        *,
        tool: str,
        channel: str,
        recipient_raw: Any,
        normalize: Callable[..., Optional[str]],
        template: Optional[str],
        params: Any,
        body: Optional[str],
        subject: Optional[str] = None,
        language: Optional[str] = None,
    ) -> ToolResult:
        """
        Shared implementation of the three send tools.

        Deliberately total: every branch returns a :class:`ToolResult` and the
        outer guard converts even programming errors into observable failures.

        Args:
            tool: Public tool name being executed.
            channel: Logical channel to send on.
            recipient_raw: Destination as supplied by the model.
            normalize: Address validator/normalizer for this channel.
            template: Template name, when templated.
            params: Raw template parameters.
            body: Pre-rendered body, when free-form.
            subject: Email subject.
            language: Template language override.

        Returns:
            A :class:`ToolResult` describing exactly one attempt.
        """
        try:
            recipient = normalize(
                recipient_raw,
                *(
                    [self.config.default_country_code]
                    if channel in {"whatsapp", "sms"}
                    else []
                ),
            )
            if not recipient:
                return self._fail(
                    tool,
                    channel,
                    recipient_raw,
                    SendErrorCode.INVALID_RECIPIENT,
                    (
                        f"'{_clip(str(recipient_raw or ''), 64)}' is not a usable "
                        f"{'email address' if channel == 'email' else 'phone number'}"
                    ),
                    provider_code="validation",
                )

            spec: Optional[TemplateSpec] = None
            param_list = _coerce_params(params)

            if template:
                spec = self.config.template(template)
                if spec is None:
                    available = ", ".join(self.config.template_names())
                    return self._fail(
                        tool,
                        channel,
                        recipient,
                        SendErrorCode.TEMPLATE_NOT_FOUND,
                        f"Unknown template '{template}'. Available: {available}",
                        provider_code="validation",
                        data={"available_templates": self.config.template_names()},
                    )
                if spec.param_count and len(param_list) != spec.param_count:
                    return self._fail(
                        tool,
                        channel,
                        recipient,
                        SendErrorCode.TEMPLATE_PARAM_MISMATCH,
                        f"Template '{template}' expects {spec.param_count} params, "
                        f"got {len(param_list)}",
                        provider_code="validation",
                        data={
                            "template": template,
                            "expected_param_count": spec.param_count,
                            "received_param_count": len(param_list),
                        },
                    )

            rendered: Optional[str] = None
            if body is not None:
                # An explicit body is the actual message text and wins over the
                # template's local preview. For WhatsApp the provider still
                # sends the real template; the preview is only for the audit log.
                rendered = _clip(body)
            elif spec is not None:
                rendered = spec.render(param_list)
            elif param_list:
                rendered = _clip(" ".join(param_list))

            if rendered is None:
                return self._fail(
                    tool,
                    channel,
                    recipient,
                    SendErrorCode.INVALID_REQUEST,
                    "Provide either a template (with params) or a body",
                    provider_code="validation",
                )

            provider = self.providers.get(channel)
            if provider is None:
                return self._fail(
                    tool,
                    channel,
                    recipient,
                    SendErrorCode.PROVIDER_UNAVAILABLE,
                    f"No provider is enabled for channel '{channel}'",
                    provider_code="no_provider",
                )

            request = SendRequest(
                to=recipient,
                template=template if (spec and channel in {"whatsapp", "sms"}) else None,
                params=param_list,
                body=rendered,
                subject=(
                    _clip(subject, 200)
                    if subject
                    else (spec.description if spec else None)
                ),
                language=(
                    language
                    or (spec.language if spec else None)
                    or self.config.meta_default_template_language
                ),
            )

            outcome = provider.send(request)
            result = ToolResult(
                tool=tool,
                success=outcome.success,
                channel=channel,
                recipient=recipient,
                message_id=outcome.message_id,
                simulated=outcome.simulated,
                data=(
                    {"template": template}
                    if template and outcome.success
                    else None
                ),
                error_code=outcome.error_code,
                error_message=outcome.error_message,
                provider_code=outcome.provider_code,
                latency_ms=outcome.latency_ms,
            )
            self._record(result, request=request, provider_code=outcome.provider_code)
            return result
        except Exception as exc:  # pragma: no cover - defensive
            return self._unexpected(tool, channel, exc)

    def _fail(
        self,
        tool: str,
        channel: str,
        recipient: Any,
        code: SendErrorCode,
        message: str,
        provider_code: Optional[str] = None,
        data: Optional[Dict[str, Any]] = None,
        kind: str = "send",
    ) -> ToolResult:
        """Build (and log) a validation-style failure result."""
        result = ToolResult(
            tool=tool,
            success=False,
            channel=channel,
            recipient=str(recipient or ""),
            kind=kind,
            error_code=code,
            error_message=message,
            provider_code=provider_code,
            data=data,
        )
        self._record(result)
        return result

    def _unexpected(
        self, tool: str, channel: str, exc: BaseException, kind: str = "send"
    ) -> ToolResult:
        """
        Last-resort guard: a bug becomes an observable failure, not a traceback.
        """
        result = ToolResult(
            tool=tool,
            success=False,
            channel=channel,
            recipient="",
            kind=kind,
            error_code=SendErrorCode.UNKNOWN,
            error_message=f"Internal tool error: {type(exc).__name__}: {exc}",
            provider_code=type(exc).__name__,
        )
        self._record(result)
        return result

    def _record(
        self,
        result: ToolResult,
        *,
        request: Optional[SendRequest] = None,
        provider_code: Optional[str] = None,
    ) -> None:
        """
        Append an audit entry for the attempt.

        Args:
            result: The result being recorded.
            request: The request that produced it, when applicable.
            provider_code: Raw provider code, when applicable.
        """
        entry: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "tool": result.tool,
            "channel": result.channel,
            "recipient": result.recipient,
            "status": result.status,
            "simulated": result.simulated,
            "message_id": result.message_id,
            "error_code": result.error_code.value if result.error_code else None,
            "provider_code": provider_code or result.provider_code,
            "error_message": result.error_message,
            "latency_ms": result.latency_ms,
        }
        if request is not None:
            entry["template"] = request.template
            entry["body_preview"] = _clip(request.body, 160)
        self.history.append(entry)

    def audit_trail(self) -> List[Dict[str, Any]]:
        """Return a copy of the attempt log."""
        return [dict(entry) for entry in self.history]

    # ------------------------------------------------------------------
    # Registry
    # ------------------------------------------------------------------

    def build_registry(self) -> "ToolRegistry":
        """Create a :class:`ToolRegistry` wired to this toolkit."""
        return ToolRegistry(self)


class ToolRegistry:
    """
    Name -> callable dispatcher used by the LLM tool-use loop.

    The model's ``tool_use`` block gives a name and an argument object; this
    class routes it to the deterministic implementation and guarantees a
    :class:`ToolResult` comes back regardless of what the model sent.
    """

    def __init__(self, toolkit: MessagingToolkit) -> None:
        """
        Args:
            toolkit: The implementation backing every tool name.
        """
        self.toolkit = toolkit
        self._tools: Dict[str, Callable[..., ToolResult]] = {}
        self.register("send_whatsapp_message", toolkit.send_whatsapp_message)
        self.register("send_sms_message", toolkit.send_sms_message)
        self.register("send_email_message", toolkit.send_email_message)
        self.register(
            "get_candidate_send_channels", toolkit.get_candidate_send_channels
        )
        self.register("list_message_templates", toolkit.list_message_templates)
        self.register("escalate_to_staff", toolkit.escalate_to_staff)

    def register(self, name: str, func: Callable[..., ToolResult]) -> None:
        """
        Register a tool implementation.

        Args:
            name: Tool name the model will call.
            func: Callable accepting keyword arguments and returning a
                :class:`ToolResult`.
        """
        self._tools[name] = func

    @property
    def names(self) -> List[str]:
        """Sorted list of registered tool names."""
        return sorted(self._tools)

    def parameters_for(self, name: str) -> List[str]:
        """Return the accepted keyword argument names of a tool."""
        func = self._tools.get(name)
        if func is None:
            return []
        return [
            param
            for param in inspect.signature(func).parameters
            if param != "self"
        ]

    def call(
        self, name: str, arguments: Optional[Mapping[str, Any]] = None
    ) -> ToolResult:
        """
        Execute a tool by name.

        Bad names and bad arguments are returned as failures rather than
        raised, so a confused model gets feedback it can act on.

        Args:
            name: Tool name requested by the model.
            arguments: Argument object from the ``tool_use`` block.

        Returns:
            The tool's :class:`ToolResult`.
        """
        args: Dict[str, Any] = dict(arguments or {})
        func = self._tools.get(name)
        if func is None:
            return ToolResult(
                tool=name,
                success=False,
                channel="none",
                recipient=str(args.get("recipient", "") or ""),
                kind="info",
                error_code=SendErrorCode.UNKNOWN,
                error_message=(
                    f"Unknown tool '{name}'. Available tools: "
                    f"{', '.join(self.names)}"
                ),
                provider_code="unknown_tool",
            )

        allowed = self.parameters_for(name)
        unexpected = [key for key in args if key not in allowed]
        if unexpected:
            return ToolResult(
                tool=name,
                success=False,
                channel="none",
                recipient=str(args.get("recipient", "") or ""),
                kind="info",
                error_code=SendErrorCode.INVALID_REQUEST,
                error_message=(
                    f"Unexpected argument(s) {sorted(unexpected)}. "
                    f"'{name}' accepts: {allowed}"
                ),
                provider_code="validation",
            )

        try:
            return func(**args)
        except TypeError as exc:
            return ToolResult(
                tool=name,
                success=False,
                channel="none",
                recipient=str(args.get("recipient", "") or ""),
                kind="info",
                error_code=SendErrorCode.INVALID_REQUEST,
                error_message=f"Bad arguments for '{name}': {exc}",
                provider_code="validation",
            )
        except Exception as exc:  # pragma: no cover - defensive
            return ToolResult(
                tool=name,
                success=False,
                channel="none",
                recipient=str(args.get("recipient", "") or ""),
                kind="info",
                error_code=SendErrorCode.UNKNOWN,
                error_message=f"Internal tool error: {type(exc).__name__}: {exc}",
                provider_code=type(exc).__name__,
            )

    @property
    def history(self) -> List[Dict[str, Any]]:
        """Audit trail of every attempt made through this registry."""
        return self.toolkit.audit_trail()


def build_tool_registry(
    config: Optional[MessagingConfig] = None,
    *,
    providers: Optional[Dict[str, MessageProvider]] = None,
    transport: Optional[HttpTransport] = None,
    smtp_connection_factory: Optional[Callable[[], Any]] = None,
    escalation_log: Optional[EscalationLog] = None,
    escalation_file: Optional[str] = None,
) -> ToolRegistry:
    """
    Build the standard registry of messaging tools.

    Args:
        config: Messaging configuration; defaults to :meth:`MessagingConfig.from_env`.
        providers: Override the channel providers (tests, custom vendors).
        transport: HTTP transport for the WhatsApp provider.
        smtp_connection_factory: Injectable SMTP connection builder.
        escalation_log: Explicit escalation sink.
        escalation_file: JSONL path for escalations when no sink is given.

    Returns:
        A ready-to-use :class:`ToolRegistry`.
    """
    toolkit = MessagingToolkit(
        config,
        providers=providers,
        transport=transport,
        smtp_connection_factory=smtp_connection_factory,
        escalation_log=escalation_log
        or (EscalationLog(escalation_file) if escalation_file else None),
    )
    return toolkit.build_registry()
