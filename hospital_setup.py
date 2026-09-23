#!/usr/bin/env python
"""
==============================================================================
 CLINIC INTEGRATION FILE -- this is the only file your hospital needs to edit.
==============================================================================

This file is the single place where you tell the patient follow-up agent:

  1. WHICH LLM to use            -> the ``LLM`` block below
  2. WHICH mailbox sends email   -> the ``EMAIL`` block below

Everything else in the project already works. Nothing here needs a programmer;
edit the three blocks, then run one command to verify.

------------------------------------------------------------------------------
 STEP 1 -- Fill in the blocks
------------------------------------------------------------------------------

    LLM    : your model provider (or switch it off to run rules-only).
    EMAIL  : your existing, already-maintained domain mailbox.
    AGENT  : who gets escalation alerts, and whether the agent may send for real.

------------------------------------------------------------------------------
 STEP 2 -- Check it (sends nothing)
------------------------------------------------------------------------------

    python hospital_setup.py --check

This reports whether the model answers and whether the mailbox accepts your
credentials, WITHOUT sending anything to a patient.

------------------------------------------------------------------------------
 STEP 3 -- Send one real test email to your own inbox
------------------------------------------------------------------------------

    python hospital_setup.py --send-test you@yourhospital.com

Only then, once you have seen the message arrive, turn on live sending
(``AGENT.enable_live_sending = True``) so the agent may contact patients.

------------------------------------------------------------------------------
 HOW EMAIL FROM YOUR DOMAIN ACTUALLY WORKS
------------------------------------------------------------------------------

The agent's email channel transmits through your own SMTP server, authenticating
as ``EMAIL.address``. Patients therefore see mail from your real domain -- for
example ``Your Dental Clinic <reminders@yourhospital.com>`` -- signed and
reputationally handled by the mail provider you already use and maintain.

That also means there is **no sender allow-list on this path**: the mailbox's own
provider governs who you may write to, exactly as it does for any other mail you
send. (The separate AWS SES tool does keep an allow-list, because the SES
sandbox only reaches verified addresses. You do not need SES at all for this.)

Two things your provider may require before it will relay for you:

* SMTP AUTH enabled for this mailbox (Microsoft 365 disables it per-mailbox by
  default -- your admin turns it on, or issues an app password).
* An app password instead of the account password, when MFA is enforced
  (Google Workspace, and most providers with 2FA). Paste it exactly as shown;
  display spaces are handled for you.

------------------------------------------------------------------------------
 A NOTE ON SAFETY
------------------------------------------------------------------------------

Live patient contact needs BOTH ``AGENT.enable_live_sending = True`` here AND
``MESSAGING_DRY_RUN=0`` in the environment. Until then every send is simulated
and printed, never transmitted -- which is what makes it safe to install this
file, run the demo and watch the agent think before it is allowed to speak to a
patient. ``--check`` and ``--send-test`` are explicit one-off opt-ins and do not
change that.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
import os
import sys

# Make the project importable when this file is run directly.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


# =============================================================================
# BLOCK 1 -- YOUR LLM
# =============================================================================


@dataclass
class LlmSettings:
    """
    Which language model decides what the agent should do next.

    The model never composes or transmits a message itself. It only chooses the
    next action from a set the clinic's own rules permit, which is why pointing
    this at any competent model is safe.

    Attributes:
        provider: ``"anthropic"`` (Claude), ``"openai"`` (any
            OpenAI-compatible endpoint), ``"azure"`` (Azure OpenAI), or
            ``"disabled"`` to run on the deterministic rule engine alone.
        model: Model id. Examples: ``claude-sonnet-4-5``, ``gpt-4o-mini``,
            ``llama3.1:8b`` (Ollama), ``Qwen/Qwen2.5-72B-Instruct`` (vLLM),
            or your Azure *deployment name*.
        api_key: Credential. Prefer leaving this blank and exporting it in the
            environment instead, so it never reaches version control.
        base_url: Endpoint root, for self-hosted or gateway deployments.
            Blank uses the vendor default. Examples:
            ``http://localhost:11434/v1`` (Ollama),
            ``https://your-gateway.internal/v1``,
            ``https://your-resource.openai.azure.com``.
        api_version: Azure only, e.g. ``2024-10-21``.
        timeout_seconds: Give slow local models room; 60s is a safe default.
        max_tokens: Cap on the model's reply length.
        organization: Optional OpenAI organization/project id.
        extra_headers: Optional extra HTTP headers, for a gateway that needs
            a routing or tenant key.
    """

    provider: str = "anthropic"
    model: str = "claude-sonnet-4-5"
    api_key: str = ""
    base_url: str = ""
    api_version: str = ""
    timeout_seconds: float = 60.0
    max_tokens: int = 1024
    organization: str = ""
    extra_headers: Dict[str, str] = field(default_factory=dict)

    # -- Ready-made settings for common deployments. Uncomment ONE and adjust.
    #
    # Claude (default):
    #   provider="anthropic", model="claude-sonnet-4-5"
    #
    # OpenAI:
    #   provider="openai", model="gpt-4o-mini"
    #
    # Azure OpenAI (model = your DEPLOYMENT name, not the model family):
    #   provider="azure", model="my-gpt4o-deployment",
    #   base_url="https://your-resource.openai.azure.com",
    #   api_version="2024-10-21"
    #
    # Local / self-hosted, no data leaves the building:
    #   provider="openai", model="llama3.1:8b",
    #   base_url="http://localhost:11434/v1"
    #
    # An in-house gateway that speaks the OpenAI API:
    #   provider="openai", model="your-model",
    #   base_url="https://llm.yourhospital.internal/v1"
    #
    # Rules only -- no model, no outbound call, fully deterministic:
    #   provider="disabled"


LLM = LlmSettings()


# =============================================================================
# BLOCK 2 -- YOUR DOMAIN MAILBOX
# =============================================================================


#: Known mail providers, so you only name one. ``(host, port, starttls, ssl)``.
SMTP_PRESETS: Dict[str, Tuple[str, int, bool, bool]] = {
    # Microsoft 365 / Exchange Online
    "microsoft365": ("smtp.office365.com", 587, True, False),
    # Google Workspace
    "google": ("smtp.gmail.com", 587, True, False),
    # Tencent Exmail (企业微信邮箱)
    "exmail": ("smtp.exmail.qq.com", 465, False, True),
    # Alibaba / Aliyun mail
    "aliyun": ("smtp.qiye.aliyun.com", 465, False, True),
    # Zoho Mail
    "zoho": ("smtp.zoho.com", 587, True, False),
    # Amazon WorkMail
    "workmail": ("smtp.mail.us-east-1.awsapps.com", 465, False, True),
    # Amazon SES SMTP interface
    "ses": ("email-smtp.ap-southeast-1.amazonaws.com", 587, True, False),
    # Fastmail
    "fastmail": ("smtp.fastmail.com", 587, True, False),
}


@dataclass
class EmailSettings:
    """
    The mailbox that will write to patients, on your own domain.

    Attributes:
        enabled: Set True once the fields below describe a real mailbox.
        address: The address patients see, e.g. ``reminders@yourhospital.com``.
            This is the authenticated mailbox and the ``From`` header.
        display_name: Friendly name shown next to the address, e.g.
            ``"BrightSmile Dental Clinic"``. Blank sends the bare address.
        preset: Name from :data:`SMTP_PRESETS` (``microsoft365``, ``google``,
            ``exmail``, ``aliyun``, ``zoho``, ...). Fills host/port/TLS for you.
        smtp_host / smtp_port / use_starttls / use_ssl: The server. Leave blank
            when ``preset`` is set.
        smtp_username: Usually the same as ``address``. Required by most
            providers even when it looks redundant.
        smtp_password: An **app password** when MFA is on, otherwise the mailbox
            password. Prefer exporting ``SMTP_PASSWORD`` in the environment over
            writing it here.
        reply_to: Optional address for patient replies when it differs from
            ``address`` (documented for the mail team; not sent by the agent).
    """

    enabled: bool = False

    address: str = ""
    display_name: str = ""

    preset: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    use_starttls: bool = True
    use_ssl: bool = False

    smtp_username: str = ""
    smtp_password: str = ""

    reply_to: str = ""

    # -- Ready-made settings. Uncomment ONE and fill in your real address.
    #
    # Microsoft 365 / Exchange Online:
    #   enabled=True, address="reminders@yourhospital.com",
    #   display_name="Your Dental Clinic", preset="microsoft365",
    #
    # Google Workspace (needs an app password):
    #   enabled=True, address="reminders@yourhospital.com",
    #   display_name="Your Dental Clinic", preset="google",
    #
    # Tencent Exmail:
    #   enabled=True, address="reminders@yourhospital.com",
    #   display_name="Your Dental Clinic", preset="exmail",
    #
    # Your own mail server:
    #   enabled=True, address="reminders@yourhospital.com",
    #   smtp_host="mail.yourhospital.com", smtp_port=587, use_starttls=True,


EMAIL = EmailSettings()


# =============================================================================
# BLOCK 3 -- HOW THE AGENT BEHAVES
# =============================================================================


@dataclass
class AgentSettings:
    """
    Operational settings for the follow-up agent itself.

    Attributes:
        escalation_email: A staff inbox alerted when the agent cannot reach a
            patient on any channel. This is the human safety valve; leaving it
            blank means escalations are recorded but nobody is told.
        enable_live_sending: ``False`` (default) prints every message instead of
            transmitting. Set ``True`` only after ``--check`` and
            ``--send-test`` have both passed, and only if you intend the agent
            to contact real patients.
        live_sends_acknowledged: A second, deliberate confirmation. Both this
            and ``enable_live_sending`` must be True, so no single edit can
            start messaging patients by accident.
        max_reminders_before_escalation: How many unanswered reminders before the
            case goes to a human.
        reminder_interval_days: Minimum gap between reminders to one patient.
        language: Default language for composed messages.
    """

    escalation_email: str = ""
    enable_live_sending: bool = False
    live_sends_acknowledged: bool = False

    max_reminders_before_escalation: int = 3
    reminder_interval_days: int = 7
    language: str = "en"


AGENT = AgentSettings()


# =============================================================================
# Below this line is machinery -- you should not need to change it.
# =============================================================================


def _resolved_smtp(email: EmailSettings) -> Dict[str, Any]:
    """
    Work out the concrete SMTP coordinates from the settings.

    An explicit host always beats a preset, so a clinic that names both gets the
    host it typed rather than a surprise from the table.

    Returns:
        Mapping with ``host``, ``port``, ``use_starttls``, ``use_ssl``.
    """
    host, port, starttls, ssl = email.smtp_host, email.smtp_port, email.use_starttls, email.use_ssl
    if not host and email.preset:
        preset = SMTP_PRESETS.get(email.preset.strip().lower())
        if preset:
            host, port, starttls, ssl = preset
    return {
        "host": host,
        "port": int(port),
        "use_starttls": bool(starttls),
        "use_ssl": bool(ssl),
    }


def environment() -> Dict[str, str]:
    """
    Render these settings as the environment variables the agent reads.

    Keeping this a pure, public function means the configuration can be
    inspected and unit-tested without touching the real environment.

    Returns:
        Variable name to value. Empty values are omitted, so an unfilled field
        cannot silently blank out a variable set elsewhere.
    """
    smtp = _resolved_smtp(EMAIL)
    values: Dict[str, str] = {}

    # --- LLM -----------------------------------------------------------------
    values["AGENT_LLM_PROVIDER"] = (LLM.provider or "anthropic").strip().lower()
    if LLM.model:
        values["AGENT_LLM_MODEL"] = LLM.model.strip()
    if LLM.api_key:
        values["AGENT_LLM_API_KEY"] = LLM.api_key.strip()
    if LLM.base_url:
        values["AGENT_LLM_BASE_URL"] = LLM.base_url.strip()
    if LLM.api_version:
        values["AGENT_LLM_API_VERSION"] = LLM.api_version.strip()
    if LLM.organization:
        values["AGENT_LLM_ORGANIZATION"] = LLM.organization.strip()
    if LLM.extra_headers:
        import json

        values["AGENT_LLM_EXTRA_HEADERS"] = json.dumps(LLM.extra_headers)
    values["AGENT_LLM_TIMEOUT_SECONDS"] = str(float(LLM.timeout_seconds))
    values["AGENT_LLM_MAX_TOKENS"] = str(int(LLM.max_tokens))
    # The decision engine's legacy knob, kept in step so both agree.
    if LLM.model:
        values["AGENT_DECISION_MODEL"] = LLM.model.strip()

    # --- Email ---------------------------------------------------------------
    if EMAIL.enabled:
        values["SMTP_HOST"] = str(smtp["host"])
        values["SMTP_PORT"] = str(smtp["port"])
        values["SMTP_USE_TLS"] = "1" if smtp["use_starttls"] else "0"
        values["SMTP_USE_SSL"] = "1" if smtp["use_ssl"] else "0"
        if EMAIL.smtp_username or EMAIL.address:
            values["SMTP_USERNAME"] = (EMAIL.smtp_username or EMAIL.address).strip()
        if EMAIL.smtp_password:
            values["SMTP_PASSWORD"] = EMAIL.smtp_password
        if EMAIL.address:
            values["EMAIL_FROM"] = EMAIL.address.strip()
        if EMAIL.display_name:
            values["EMAIL_FROM_NAME"] = EMAIL.display_name.strip()

    # --- Agent ---------------------------------------------------------------
    if AGENT.escalation_email:
        values["AGENT_ESCALATION_EMAIL"] = AGENT.escalation_email.strip()
    values["AGENT_MAX_REMINDERS_BEFORE_ESCALATION"] = str(
        int(AGENT.max_reminders_before_escalation)
    )
    values["AGENT_REMINDER_INTERVAL_DAYS"] = str(int(AGENT.reminder_interval_days))

    # --- Live sending: two independent switches, both required -----------------
    live = AGENT.enable_live_sending and AGENT.live_sends_acknowledged
    values["AGENT_LIVE_SENDS"] = "1" if live else "0"
    values["MESSAGING_DRY_RUN"] = "0" if live else "1"

    return {key: value for key, value in values.items() if value != ""}


def apply(*, override: bool = True) -> int:
    """
    Load these settings into the process environment.

    Call this before constructing the agent, or set ``CLINIC_SETUP_FILE`` so the
    agent finds it automatically. Existing variables win when ``override`` is
    False, which lets a deployment inject secrets from a vault instead.

    Args:
        override: Whether to replace variables already set in the environment.

    Returns:
        Number of variables applied.
    """
    applied = 0
    for key, value in environment().items():
        if override or key not in os.environ:
            os.environ[key] = value
            applied += 1
    return applied


def validate() -> List[str]:
    """
    Check the settings for the mistakes that actually happen.

    Returns:
        Human-readable problems. Empty means the configuration is coherent.
    """
    problems: List[str] = []

    provider = (LLM.provider or "").strip().lower()
    known = {"anthropic", "openai", "azure", "disabled", "none", "off"}
    if provider not in known and provider not in {
        "openai_compatible", "openai-compatible", "custom", "ollama", "vllm"
    }:
        problems.append(
            f"LLM.provider {LLM.provider!r} is not recognised; use anthropic, "
            "openai, azure or disabled."
        )
    if provider in {"openai", "azure"} and not LLM.model:
        problems.append("LLM.model is required for an OpenAI-compatible provider.")
    if provider == "azure" and not LLM.base_url:
        problems.append("LLM.base_url is required for Azure (your resource endpoint).")
    if provider not in {"disabled", "none", "off"} and not (
        LLM.api_key or _env_llm_key(provider)
    ):
        problems.append(
            "No LLM credential found. Set LLM.api_key here, or export "
            f"{'ANTHROPIC_API_KEY' if provider == 'anthropic' else 'OPENAI_API_KEY'}"
            " or AGENT_LLM_API_KEY in the environment."
        )

    if EMAIL.enabled:
        smtp = _resolved_smtp(EMAIL)
        if not smtp["host"]:
            problems.append("EMAIL.smtp_host is required (or set EMAIL.preset).")
        if EMAIL.preset and EMAIL.preset.strip().lower() not in SMTP_PRESETS:
            problems.append(
                f"EMAIL.preset {EMAIL.preset!r} is unknown; known presets: "
                + ", ".join(sorted(SMTP_PRESETS))
            )
        if not EMAIL.address:
            problems.append("EMAIL.address is required: the mailbox that sends.")
        elif "@" not in EMAIL.address:
            problems.append(f"EMAIL.address {EMAIL.address!r} is not an email address.")
        if not (EMAIL.smtp_username or EMAIL.address):
            problems.append("EMAIL.smtp_username is required to authenticate.")
        if not (EMAIL.smtp_password or os.environ.get("SMTP_PASSWORD")):
            problems.append(
                "EMAIL.smtp_password is required. Use an app password when MFA "
                "is enabled, or export SMTP_PASSWORD instead of storing it here."
            )
        if smtp["use_ssl"] and smtp["use_starttls"]:
            problems.append(
                "Set only one of use_ssl (port 465) and use_starttls (port 587)."
            )
    else:
        problems.append(
            "EMAIL.enabled is False: patients will not receive email. Set it True "
            "once the mailbox details below are filled in."
        )

    if AGENT.enable_live_sending and not AGENT.live_sends_acknowledged:
        problems.append(
            "AGENT.enable_live_sending is True but live_sends_acknowledged is "
            "False, so the agent stays in simulation. Set both to send for real."
        )
    if AGENT.enable_live_sending and not AGENT.escalation_email:
        problems.append(
            "Set AGENT.escalation_email before enabling live sending: when no "
            "channel can reach a patient, a human must be told."
        )
    return problems


def load_dotenv() -> None:
    """
    Load the project's ``.env`` into the environment, if there is one.

    The blocks below are for values a clinic is happy to keep in the file. The
    guide tells them to prefer exporting secrets instead, so the checks must
    actually read an exported ``SMTP_PASSWORD`` -- otherwise the recommended,
    safer setup reports a spurious "smtp_password is required".

    Existing variables win, and a missing file is not an error, so this is safe
    to call from every entry point.
    """
    try:
        from tools.config import load_env_file
    except ImportError:  # pragma: no cover - tools/ is always importable
        return
    load_env_file()


def _missing_llm_credential() -> Optional[str]:
    """
    Describe the missing credential, or ``None`` when there is nothing missing.

    Checked *before* the live model call so the common misconfiguration -- no
    key at all -- is reported as itself. Otherwise a missing SDK or an
    unauthenticated endpoint is what surfaces, and "install the anthropic
    package" is the wrong advice for someone who simply has not pasted a key.
    """
    provider = (LLM.provider or "anthropic").strip().lower()
    if provider in {"disabled", "none", "off"}:
        return None
    if LLM.api_key or _env_llm_key(provider):
        return None
    return (
        "No LLM credential: set LLM.api_key in this file, or export "
        f"{'ANTHROPIC_API_KEY' if provider == 'anthropic' else 'OPENAI_API_KEY'} "
        "or AGENT_LLM_API_KEY. Set LLM.provider = \"disabled\" to run rules-only."
    )


def _env_llm_key(provider: str) -> Optional[str]:

    """Find a credential already present in the environment (never returned)."""
    names = ["AGENT_LLM_API_KEY", "LLM_API_KEY"]
    names.append("ANTHROPIC_API_KEY" if provider == "anthropic" else "OPENAI_API_KEY")
    for name in names:
        if str(os.environ.get(name) or "").strip():
            return name
    return None


def summary() -> Dict[str, Any]:
    """
    Describe the resolved configuration without revealing any secret.

    Returns:
        JSON-serializable summary for ``--show`` and for support tickets.
    """
    smtp = _resolved_smtp(EMAIL)
    return {
        "llm": {
            "provider": (LLM.provider or "anthropic").strip().lower(),
            "model": LLM.model,
            "endpoint": LLM.base_url or "(vendor default)",
            "credential": "set" if (LLM.api_key or _env_llm_key(LLM.provider)) else "MISSING",
        },
        "email": {
            "enabled": EMAIL.enabled,
            "from": _format_from(),
            "server": f"{smtp['host']}:{smtp['port']}" if smtp["host"] else "(not set)",
            "encryption": "ssl" if smtp["use_ssl"] else (
                "starttls" if smtp["use_starttls"] else "none"
            ),
            "credential": (
                "set" if (EMAIL.smtp_password or os.environ.get("SMTP_PASSWORD")) else "MISSING"
            ),
        },
        "agent": {
            "escalation_email": AGENT.escalation_email or "(not set)",
            "live_sending": AGENT.enable_live_sending and AGENT.live_sends_acknowledged,
        },
        "problems": validate(),
    }


def _format_from() -> str:
    """Render the ``From`` header the way a patient will see it."""
    if not EMAIL.address:
        return "(not set)"
    if EMAIL.display_name:
        return f"{EMAIL.display_name} <{EMAIL.address}>"
    return EMAIL.address


def check_llm() -> Tuple[bool, str]:
    """
    Ask the configured model one trivial question.

    A real call, because a wrong model name, a revoked key and a typo in a
    self-hosted URL all look identical until you actually try.

    Returns:
        ``(ok, message)``. Never raises.
    """
    load_dotenv()
    apply()
    missing = _missing_llm_credential()
    if missing:
        return False, missing
    try:
        from tools.llm_providers import LlmProviderConfig, create_llm_client

        config = LlmProviderConfig.from_env()
        if config.is_disabled:
            return True, "disabled on purpose: the agent will use its rule engine."
        client = create_llm_client(config)
        if client is None:
            return True, "no model configured: the agent will use its rule engine."
        response = client.messages.create(
            model=config.model,
            max_tokens=16,
            system="Reply with the single word: ready",
            messages=[{"role": "user", "content": "Confirm you are reachable."}],
        )
        text = _first_text(response)
        return True, f"{config.kind} / {config.model} answered: {text[:80]!r}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def check_email() -> Tuple[bool, str]:
    """
    Connect and authenticate to the clinic's SMTP server, then disconnect.

    Logs in but does not send, so it is safe to run at any time. This catches
    the three failures that matter: wrong host, SMTP AUTH disabled for the
    mailbox, and a bad or grouped app password.

    Returns:
        ``(ok, message)``. Never raises.
    """
    load_dotenv()
    apply()
    if not EMAIL.enabled:
        return False, "EMAIL.enabled is False; fill in the mailbox block first."

    smtp = _resolved_smtp(EMAIL)
    import smtplib

    from tools.tls import default_ssl_context

    connection = None
    try:
        if smtp["use_ssl"]:
            connection = smtplib.SMTP_SSL(
                smtp["host"], smtp["port"], timeout=LLM.timeout_seconds,
                context=default_ssl_context(),
            )
        else:
            connection = smtplib.SMTP(
                smtp["host"], smtp["port"], timeout=LLM.timeout_seconds
            )
            connection.ehlo()
            if smtp["use_starttls"]:
                connection.starttls(context=default_ssl_context())
                connection.ehlo()
        connection.login(
            (EMAIL.smtp_username or EMAIL.address),
            EMAIL.smtp_password or os.environ.get("SMTP_PASSWORD", ""),
        )
        return True, (
            f"authenticated to {smtp['host']}:{smtp['port']} as "
            f"{EMAIL.smtp_username or EMAIL.address}"
        )
    except smtplib.SMTPAuthenticationError as exc:
        return False, (
            f"the server rejected the mailbox credentials ({exc}). Check the "
            "username, and use an app password if MFA is enabled."
        )
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    finally:
        if connection is not None:
            try:
                connection.quit()
            except Exception:
                pass


def send_test_email(recipient: str) -> Tuple[bool, str]:
    """
    Send one real email through the same path the agent uses.

    This is the end-to-end proof: the message goes through the agent's own
    delivery tool, authenticating as the clinic mailbox, so a message that
    arrives means patient reminders will arrive too.

    Args:
        recipient: Where to send the test. Use your own inbox.

    Returns:
        ``(ok, message)``. Never raises.
    """
    # Load the project .env, then apply the blocks -- and only *then* bypass the
    # simulation switch, for this one explicit call. The order matters: apply()
    # writes MESSAGING_DRY_RUN from the AGENT block, so setting it earlier would
    # be overwritten and --send-test would report success while simulating.
    load_dotenv()
    apply()
    os.environ["MESSAGING_DRY_RUN"] = "0"
    try:
        from tools.config import MessagingConfig
        from tools.messaging import build_tool_registry

        config = MessagingConfig.from_env()
        if config.simulates:  # pragma: no cover - guarded against the bug above
            return False, (
                "refusing to report success: the send would be simulated "
                "(MESSAGING_DRY_RUN is still on)"
            )
        registry = build_tool_registry(config=config)
        result = registry.call(
            "send_email_message",
            {
                "recipient": recipient,
                "subject": f"Test from {EMAIL.display_name or EMAIL.address}",
                "body": (
                    "This is a delivery test from your patient follow-up agent.\n\n"
                    f"Sent through {_format_from()}.\n"
                    "If you can read this in your inbox, your domain mailbox is "
                    "correctly configured and the agent can email patients."
                ),
            },
        )
        payload = result.to_payload()
        if payload.get("status") == "sent":
            return True, f"sent to {recipient} (message_id={payload.get('message_id')})"
        return False, (
            f"the send failed: {payload.get('error_code')} -- "
            f"{payload.get('error_message')}"
        )
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def _first_text(response: Any) -> str:
    """Pull the first text block out of an Anthropic-shaped model response."""
    content = response.get("content") if isinstance(response, dict) else getattr(
        response, "content", None
    )
    for block in content or []:
        if isinstance(block, dict) and block.get("type") == "text":
            return str(block.get("text") or "")
        text = getattr(block, "text", None)
        if text:
            return str(text)
    return ""


def main(argv: Optional[List[str]] = None) -> int:
    """
    Command line entry point.

    Returns:
        Process exit code: 0 on success, 1 when a check fails.
    """
    args = list(sys.argv[1:] if argv is None else argv)

    # Read the project .env first, so --show/--check and validate() agree with
    # what the agent would actually see at runtime.
    load_dotenv()

    if not args or "--help" in args or "-h" in args:
        print(__doc__)
        return 0

    if "--show" in args:
        import json

        print(json.dumps(summary(), indent=2, ensure_ascii=False))
        return 0

    if "--check" in args:
        print("=" * 70)
        print("CLINIC INTEGRATION CHECK (sends nothing)")
        print("=" * 70)
        print(f"  From address : {_format_from()}")
        print(f"  LLM          : {(LLM.provider or 'anthropic')} / {LLM.model or '(default)'}")
        print()
        problems = validate()
        if problems:
            print("Configuration problems:")
            for item in problems:
                print(f"  - {item}")
            print()
        ok_llm, message_llm = check_llm()
        print(f"  [{'PASS' if ok_llm else 'FAIL'}] model      : {message_llm}")
        ok_mail, message_mail = check_email()
        print(f"  [{'PASS' if ok_mail else 'FAIL'}] mailbox    : {message_mail}")
        if AGENT.escalation_email:
            print(f"  [info] escalations go to {AGENT.escalation_email}")
        live = AGENT.enable_live_sending and AGENT.live_sends_acknowledged
        print(
            f"  [info] agent live sending: {'ON' if live else 'OFF (simulating)'}"
        )
        print()
        if ok_llm and ok_mail and not problems:
            print("Ready. Next: python hospital_setup.py --send-test you@yourhospital.com")
            return 0
        print("Fix the items marked FAIL or listed above, then re-run --check.")
        return 1

    if "--send-test" in args:
        index = args.index("--send-test")
        if index + 1 >= len(args) or args[index + 1].startswith("-"):
            print("Usage: python hospital_setup.py --send-test you@yourhospital.com")
            return 1
        recipient = args[index + 1]
        ok, message = send_test_email(recipient)
        print(f"[{'PASS' if ok else 'FAIL'}] test email: {message}")
        return 0 if ok else 1

    print(f"Unknown option: {' '.join(args)}")
    print("Use --check, --send-test <address>, --show or --help.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
