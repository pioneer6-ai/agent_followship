"""
Configuration for the messaging tool layer.

Everything is environment-driven so that credentials never live in source
(``.env`` is already git-ignored). Two ideas matter here:

1. **Templates.** Business-initiated WhatsApp messages must use a pre-approved
   template once the customer-service window closes, so templates are a
   first-class, validated concept rather than free text.
2. **Dry-run.** With no credentials the tools still work end-to-end by
   simulating sends, so the agent loop, demos and tests run without keys.
   Live sending is **opt-in**: unless ``MESSAGING_DRY_RUN=0`` is set, sends
   are simulated even when credentials are present, so a filled-in ``.env``
   can never message real patients by accident.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional
import json
import os
import re


@dataclass(frozen=True)
class TemplateSpec:
    """
    A message template the agent is allowed to use.

    Attributes:
        name: Template identifier as the LLM will request it.
        param_count: Number of positional body parameters (validated before send).
        description: Human/LLM-readable purpose of the template.
        body_preview: Local rendering used for SMS/email and as a fallback body.
            Uses ``{1}``, ``{2}`` ... placeholders.
        language: Language code expected by the provider.
    """

    name: str
    param_count: int
    description: str
    body_preview: str = ""
    language: str = "en"

    def render(self, params: Optional[List[str]] = None) -> str:
        """
        Substitute positional parameters into :attr:`body_preview`.

        Uses plain token replacement rather than ``str.format`` so that
        patient-supplied text containing braces cannot break rendering.

        Args:
            params: Positional parameters, 1-indexed in the preview text.

        Returns:
            Rendered message text.
        """
        text = self.body_preview
        for index, value in enumerate(params or [], start=1):
            text = text.replace("{%d}" % index, str(value))
        return text.strip()


DEFAULT_TEMPLATES: Dict[str, TemplateSpec] = {
    "appointment_reminder": TemplateSpec(
        name="appointment_reminder",
        param_count=3,
        description="Routine reminder that a follow-up appointment is due.",
        body_preview=(
            "Hello {1}, it's time for your {2} follow-up at our clinic. "
            "Reply to this message or call {3} to book a time."
        ),
    ),
    "appointment_urgent_followup": TemplateSpec(
        name="appointment_urgent_followup",
        param_count=2,
        description="Higher-urgency reminder for a significantly overdue patient.",
        body_preview=(
            "Hello {1}, our records show your follow-up is {2} days overdue. "
            "Please contact us as soon as possible to arrange your visit."
        ),
    ),
    "appointment_confirmation": TemplateSpec(
        name="appointment_confirmation",
        param_count=2,
        description="Confirms a booked appointment slot.",
        body_preview=(
            "Hello {1}, your appointment is confirmed for {2}. "
            "Please contact us if you need to reschedule."
        ),
    ),
    "appointment_slot_proposal": TemplateSpec(
        name="appointment_slot_proposal",
        param_count=4,
        description="Offers up to three candidate appointment slots.",
        body_preview=(
            "Hello {1}, we have these times available: {2}, {3} or {4}. "
            "Reply with your preferred option."
        ),
    ),
    "appointment_reschedule": TemplateSpec(
        name="appointment_reschedule",
        param_count=2,
        description="Acknowledges a rescheduled appointment.",
        body_preview=(
            "Hello {1}, your appointment has been moved to {2}. "
            "Thank you for letting us know."
        ),
    ),
}


def _env_bool(
    source: Mapping[str, str], name: str, default: Optional[bool] = None
) -> Optional[bool]:
    """Parse a boolean value (``1/true/yes/on``); ``None``/blank keeps default."""
    raw = source.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _env_int(source: Mapping[str, str], name: str, default: int) -> int:
    """Parse an integer value, falling back on garbage."""
    raw = source.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return int(str(raw).strip())
    except ValueError:
        return default


def _env_float(source: Mapping[str, str], name: str, default: float) -> float:
    """Parse a float value, falling back on garbage."""
    raw = source.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return float(str(raw).strip())
    except ValueError:
        return default


def _clean_country_code(raw: Optional[str]) -> Optional[str]:
    """
    Normalize a default country calling code to ``"+<digits>"`` form.

    Accepts ``86``, ``+86`` or ``0086``; returns ``None`` for blank/garbage.
    """
    if raw is None:
        return None
    digits = re.sub(r"\D", "", str(raw))
    if not digits:
        return None
    digits = digits.lstrip("0")
    return f"+{digits}" if digits else None


def _env_list(
    source: Mapping[str, str], name: str, default: List[str]
) -> List[str]:
    """
    Parse a comma-separated environment variable into a list.

    The parsed value **replaces** ``default`` rather than extending it, so a
    deployment can narrow or widen the allow-list without code changes. A blank
    or unset variable keeps the default.

    Args:
        source: Environment mapping.
        name: Variable name.
        default: Value used when the variable is unset/blank.

    Returns:
        The resolved list.
    """
    raw = source.get(name)
    if raw is None or str(raw).strip() == "":
        return list(default)
    return [item.strip() for item in str(raw).split(",") if item.strip()]


def _digits(value: Any) -> str:
    """Return only the digits of a phone number, for comparison."""
    return re.sub(r"\D", "", str(value or ""))


def _normalize_app_password(value: Any) -> Optional[str]:
    """
    Drop the display spaces from an application password.

    Gmail and other providers show app passwords in groups (``abcd efgh ijkl
    mnop``) but the SMTP ``AUTH`` exchange wants the 16 characters with no
    separators: a spaced password makes Gmail hang up mid-connection, which
    surfaces as a confusing ``provider_unavailable`` rather than a bad
    credential. Normalization only applies when the value is unambiguously an
    app password -- exactly 16 alphanumerics once whitespace is removed -- so a
    conventional password is passed through untouched.
    """
    if value is None:
        return None
    text = str(value)
    squashed = re.sub(r"\s+", "", text)
    if squashed != text and len(squashed) == 16 and squashed.isalnum():
        return squashed
    return text


@dataclass
class MessagingConfig:
    """
    Resolved configuration for all outbound channels.

    Attributes:
        whatsapp_provider: ``meta`` | ``twilio`` | ``none``.
        dry_run: ``True`` force-simulate, ``False`` allow real sends, ``None``
            means "not configured explicitly", which also simulates.
        timeout_seconds: Per-request timeout for provider calls.
        templates: Approved templates keyed by name.
        meta_*: Meta WhatsApp Cloud API settings.
        twilio_*: Twilio settings (shared by WhatsApp and SMS).
        smtp_* / email_from: Outbound email settings.
        aws_* / aws_sms_allowlist / aws_email_allowlist: AWS region, SES sender,
            SES configuration set and the verified-recipient allow-lists
            enforced by ``send_sms`` / ``send_email``.
    """

    # Channel selection
    whatsapp_provider: str = "meta"
    dry_run: Optional[bool] = None
    timeout_seconds: float = 10.0
    default_country_code: Optional[str] = None

    # Templates
    templates: Dict[str, TemplateSpec] = field(
        default_factory=lambda: dict(DEFAULT_TEMPLATES)
    )

    # Meta WhatsApp Cloud API
    meta_access_token: Optional[str] = None
    meta_phone_number_id: Optional[str] = None
    meta_api_version: str = "v21.0"
    meta_default_template_language: str = "en"

    # Twilio (WhatsApp + SMS)
    twilio_account_sid: Optional[str] = None
    twilio_auth_token: Optional[str] = None
    twilio_whatsapp_from: Optional[str] = None  # e.g. whatsapp:+14155238886
    twilio_sms_from: Optional[str] = None       # e.g. +14155238886
    twilio_content_sids: Dict[str, str] = field(default_factory=dict)
    twilio_require_content_sid: bool = False

    # Email
    smtp_host: Optional[str] = None
    smtp_port: int = 587
    smtp_username: Optional[str] = None
    smtp_password: Optional[str] = None
    smtp_use_tls: bool = True
    smtp_use_ssl: bool = False
    email_from: Optional[str] = None

    # AWS End User Messaging SMS (pinpoint-sms-voice-v2) + SES.
    # While the account is in the SMS sandbox / SES sandbox, only verified
    # destinations may receive anything, so the allow-lists below are enforced
    # by the ``send_sms`` / ``send_email`` tools before any API call is made.
    aws_region: str = "ap-southeast-1"
    aws_ses_source: str = "martinchenonly1@gmail.com"
    #: Optional SES configuration set. When set, every ``send_email`` call is
    #: tagged with it so SES publishes per-send Delivery/Bounce/Complaint
    #: events -- the only way to prove a message was delivered rather than
    #: merely accepted.
    aws_ses_configuration_set: Optional[str] = None
    aws_sms_origination_identity: Optional[str] = None
    aws_sms_allowlist: List[str] = field(
        default_factory=lambda: ["+6583536885"]
    )
    aws_email_allowlist: List[str] = field(
        default_factory=lambda: ["martinchenonly1@gmail.com"]
    )

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "MessagingConfig":
        """
        Build a configuration from environment variables.

        Args:
            env: Optional mapping to read instead of ``os.environ`` (tests).

        Returns:
            Populated :class:`MessagingConfig`.
        """
        source: Mapping[str, str] = os.environ if env is None else env

        content_sids: Dict[str, str] = {}
        raw_sids = str(source.get("TWILIO_CONTENT_SIDS", "") or "").strip()
        if raw_sids:
            try:
                parsed = json.loads(raw_sids)
                if isinstance(parsed, dict):
                    content_sids = {str(k): str(v) for k, v in parsed.items()}
            except ValueError:
                content_sids = {}

        return cls(
            whatsapp_provider=str(
                source.get("WHATSAPP_PROVIDER", "meta") or "meta"
            ).strip().lower(),
            dry_run=_env_bool(source, "MESSAGING_DRY_RUN", True),
            timeout_seconds=_env_float(source, "MESSAGING_TIMEOUT_SECONDS", 10.0),
            default_country_code=_clean_country_code(
                source.get("MESSAGING_DEFAULT_COUNTRY_CODE")
            ),
            meta_access_token=source.get("META_WHATSAPP_ACCESS_TOKEN"),
            meta_phone_number_id=source.get("META_WHATSAPP_PHONE_NUMBER_ID"),
            meta_api_version=str(source.get("META_GRAPH_API_VERSION", "v21.0")),
            meta_default_template_language=str(
                source.get("META_TEMPLATE_LANGUAGE", "en")
            ),
            twilio_account_sid=source.get("TWILIO_ACCOUNT_SID"),
            twilio_auth_token=source.get("TWILIO_AUTH_TOKEN"),
            twilio_whatsapp_from=source.get("TWILIO_WHATSAPP_FROM"),
            twilio_sms_from=source.get("TWILIO_SMS_FROM"),
            twilio_content_sids=content_sids,
            twilio_require_content_sid=bool(
                _env_bool(source, "TWILIO_REQUIRE_CONTENT_SID", False)
            ),
            smtp_host=source.get("SMTP_HOST"),
            smtp_port=_env_int(source, "SMTP_PORT", 587),
            smtp_username=source.get("SMTP_USERNAME"),
            smtp_password=_normalize_app_password(source.get("SMTP_PASSWORD")),
            smtp_use_tls=bool(_env_bool(source, "SMTP_USE_TLS", True)),
            smtp_use_ssl=bool(_env_bool(source, "SMTP_USE_SSL", False)),
            email_from=source.get("EMAIL_FROM") or source.get("SMTP_USERNAME"),
            aws_region=str(
                source.get("AWS_REGION") or source.get("AWS_DEFAULT_REGION") or ""
            ).strip()
            or "ap-southeast-1",
            aws_ses_source=str(
                source.get("AWS_SES_SOURCE") or ""
            ).strip()
            or "martinchenonly1@gmail.com",
            aws_ses_configuration_set=(
                str(source.get("AWS_SES_CONFIGURATION_SET") or "").strip() or None
            ),
            aws_sms_origination_identity=(
                str(source.get("AWS_SMS_ORIGINATION_IDENTITY") or "").strip()
                or None
            ),
            aws_sms_allowlist=_env_list(
                source, "AWS_SMS_ALLOWED_NUMBERS", ["+6583536885"]
            ),
            aws_email_allowlist=_env_list(
                source, "AWS_EMAIL_ALLOWED_ADDRESSES", ["martinchenonly1@gmail.com"]
            ),
        )

    def template(self, name: Optional[str]) -> Optional[TemplateSpec]:
        """
        Look up an approved template by name.

        Args:
            name: Template name, possibly ``None``.

        Returns:
            The :class:`TemplateSpec`, or ``None`` when unknown.
        """
        if not name:
            return None
        return self.templates.get(name)

    def template_names(self) -> List[str]:
        """Return the sorted list of approved template names."""
        return sorted(self.templates)

    @property
    def live_requested(self) -> bool:
        """
        Whether real transmission was explicitly opted into.

        Only ``MESSAGING_DRY_RUN=0`` sets this; the default (unset or ``1``)
        keeps every send simulated, so a populated ``.env`` alone can never
        message a real patient.
        """
        return self.dry_run is False

    @property
    def simulates(self) -> bool:
        """
        Whether sends are simulated instead of transmitted.

        A channel that is not configured still fails loudly with
        ``config_missing`` when live mode is requested; callers must therefore
        check :attr:`live_requested` and the provider's configuration before
        treating this as "nothing to worry about".
        """
        return not self.live_requested

    def is_sms_recipient_allowed(self, recipient: Any) -> bool:
        """
        Whether a phone number is a permitted AWS SMS destination.

        Comparison is digit-only, so ``+6583536885``, ``65 8353 6885`` and
        ``6583536885`` all match the same allow-list entry.

        Fails **closed**: an empty allow-list permits nothing.

        Args:
            recipient: Candidate phone number.

        Returns:
            True when the number is on the allow-list.
        """
        allowed = {
            _digits(number) for number in self.aws_sms_allowlist if _digits(number)
        }
        candidate = _digits(recipient)
        return bool(candidate) and candidate in allowed

    def is_email_recipient_allowed(self, recipient: Any) -> bool:
        """
        Whether an address is a permitted SES destination.

        Comparison is case- and whitespace-insensitive. Fails **closed**: an
        empty allow-list permits nothing.

        Args:
            recipient: Candidate email address.

        Returns:
            True when the address is on the allow-list.
        """
        allowed = {
            str(address).strip().lower()
            for address in self.aws_email_allowlist
            if str(address or "").strip()
        }
        candidate = str(recipient or "").strip().lower()
        return bool(candidate) and candidate in allowed
