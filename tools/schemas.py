"""
Anthropic-style tool schemas for the messaging tools.

These are the prompts that matter most: the model only ever learns *how* to
behave around failures from the tool descriptions. Each description therefore
states the contract explicitly -- "the tool never raises, a failure returns
``status: "failed"`` with a normalized ``error_code``, and you must decide what
to do next".

Every tool answers with a JSON object whose ``status`` says what happened:
``"sent"`` (a real delivery attempt succeeded), ``"failed"`` (an attempt did
not succeed -- read the guidance fields), ``"ok"`` (an informational tool
answered) or ``"escalated"`` (a human hand-off was recorded).

The names here are validated against :data:`tools.messaging.TOOL_NAMES` so the
schemas and the implementations cannot drift apart.
"""

from __future__ import annotations
from typing import Any, Dict, List, Optional, Sequence

from tools.messaging import TOOL_NAMES

#: Shared wording explaining the failure protocol to the model.
_FAILURE_PROTOCOL = (
    " This tool never raises: it always returns an object with a 'status' field "
    "of 'sent' or 'failed'. On 'failed' read 'error_code' (normalized reason), "
    "'retryable' (whether the SAME channel may work later), "
    "'suggested_fallback_channels' (other channels worth trying) and 'hint'. "
    "Do not repeat a send that failed with a non-retryable error on the same "
    "channel; switch channel or call escalate_to_staff."
)

#: Shared wording for the tools that only answer questions.
_INFO_PROTOCOL = (
    " This tool never raises and sends nothing: it returns 'status': 'ok' with "
    "the answer under 'data'. Use the answer to choose your next action."
)

_SEND_WHATSAPP_SCHEMA: Dict[str, Any] = {
    "name": "send_whatsapp_message",
    "description": (
        "Send one WhatsApp message to a patient and report exactly what "
        "happened. For business-initiated messages (outside the patient's "
        "24-hour reply window) you MUST pass an approved 'template' plus its "
        "'params'; free-form 'body' text only works inside that window."
        + _FAILURE_PROTOCOL
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "recipient": {
                "type": "string",
                "description": "Patient phone number in E.164 form, e.g. '+15550001111'.",
            },
            "template": {
                "type": "string",
                "description": (
                    "Approved template name from list_message_templates, e.g. "
                    "'appointment_reminder'."
                ),
            },
            "params": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Positional template parameters in template order; the count "
                    "must match what the template expects."
                ),
            },
            "body": {
                "type": "string",
                "description": (
                    "Free-form message text. Only valid inside the 24-hour "
                    "customer-service window; prefer 'template' + 'params'."
                ),
            },
            "language": {
                "type": "string",
                "description": "Template language code, e.g. 'en'. Optional.",
            },
        },
        "required": ["recipient"],
    },
}

_SEND_SMS_SCHEMA: Dict[str, Any] = {
    "name": "send_sms_message",
    "description": (
        "Send one SMS to a patient. Use this when WhatsApp is unavailable, "
        "unverified for this recipient, or the patient simply prefers SMS."
        + _FAILURE_PROTOCOL
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "recipient": {
                "type": "string",
                "description": "Patient phone number in E.164 form, e.g. '+15550001111'.",
            },
            "body": {
                "type": "string",
                "description": (
                    "The SMS text. Keep it short and include how to reach the "
                    "clinic."
                ),
            },
            "template": {
                "type": "string",
                "description": (
                    "Optional catalogue template name. When 'body' is omitted the "
                    "template text is rendered and sent instead."
                ),
            },
            "params": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Positional parameters for 'template'.",
            },
        },
        "required": ["recipient"],
    },
}

_SEND_EMAIL_SCHEMA: Dict[str, Any] = {
    "name": "send_email_message",
    "description": (
        "Send one email to a patient. Email is the safest fallback: it needs no "
        "opt-in or messaging window. Prefer an email address the clinic already "
        "holds." + _FAILURE_PROTOCOL
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "recipient": {
                "type": "string",
                "description": "Patient email address, e.g. 'alex@example.com'.",
            },
            "subject": {
                "type": "string",
                "description": "Email subject line.",
            },
            "body": {
                "type": "string",
                "description": "Plain-text email body.",
            },
            "template": {
                "type": "string",
                "description": (
                    "Optional catalogue template name whose text is used when "
                    "'body' is omitted."
                ),
            },
            "params": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Positional parameters for 'template'.",
            },
        },
        "required": ["recipient"],
    },
}

_AWS_SEND_SMS_SCHEMA: Dict[str, Any] = {
    "name": "send_sms",
    "description": (
        "Send one SMS via AWS End User Messaging SMS (pinpoint-sms-voice-v2). "
        "Use this when SMS is the right channel; it is a separate path from "
        "send_sms_message. The AWS account is still in its sandbox, so the "
        "recipient must be one of the verified destination numbers -- any "
        "other number is refused before transmission."
        + _FAILURE_PROTOCOL
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "phone_number": {
                "type": "string",
                "description": "E.164格式，如+6583536885",
            },
            "message": {
                "type": "string",
                "description": "SMS text to deliver.",
            },
            "reason": {
                "type": "string",
                "description": "agent判断需要发送的理由，用于审计",
            },
        },
        "required": ["phone_number", "message", "reason"],
    },
}

_AWS_SEND_EMAIL_SCHEMA: Dict[str, Any] = {
    "name": "send_email",
    "description": (
        "Send one email via Amazon SES. Use this when email is the right "
        "channel; it is a separate path from send_email_message. The AWS "
        "account is still in the SES sandbox, so the recipient must be one of "
        "the verified addresses -- any other address is refused before "
        "transmission."
        + _FAILURE_PROTOCOL
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "to_email": {
                "type": "string",
                "description": "Recipient email address (must be SES-verified).",
            },
            "subject": {
                "type": "string",
                "description": "Email subject line.",
            },
            "body": {
                "type": "string",
                "description": "Plain-text email body.",
            },
            "reason": {
                "type": "string",
                "description": (
                    "Why the agent decided this send is necessary; recorded "
                    "for the audit trail."
                ),
            },
        },
        "required": ["to_email", "subject", "body", "reason"],
    },
}

_CANDIDATE_CHANNELS_SCHEMA: Dict[str, Any] = {
    "name": "get_candidate_send_channels",
    "description": (
        "Ask which channels are still worth trying. Call this after a failed "
        "send, or when you are unsure which channel to use. It returns "
        "'fallback_order' (recommended order) and per-channel availability, "
        "honouring the patient's preferred channel and skipping the channel "
        "that just failed." + _INFO_PROTOCOL
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "preferred_channel": {
                "type": "string",
                "enum": ["whatsapp", "sms", "email"],
                "description": "The patient's preferred channel, if known.",
            },
            "failed_channel": {
                "type": "string",
                "enum": ["whatsapp", "sms", "email"],
                "description": "A channel that just failed and should be skipped.",
            },
            "error_code": {
                "type": "string",
                "description": (
                    "The 'error_code' from that failure, e.g. "
                    "'recipient_not_verified'. Used to rank the alternatives."
                ),
            },
        },
        "required": [],
    },
}

_LIST_TEMPLATES_SCHEMA: Dict[str, Any] = {
    "name": "list_message_templates",
    "description": (
        "List the approved message templates with their names, languages, "
        "descriptions and how many positional parameters each one expects. Call "
        "this before templated sends if you are not certain of a template name "
        "or parameter count." + _INFO_PROTOCOL
    ),
    "input_schema": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}

_ESCALATE_SCHEMA: Dict[str, Any] = {
    "name": "escalate_to_staff",
    "description": (
        "Hand this case to a human at the clinic. Call this when every messaging "
        "channel has failed, when the failure is non-retryable and has no "
        "fallback channel (e.g. 'auth_failed', 'opted_out', 'config_missing'), "
        "or when the patient asks for a person. Recorded escalations are "
        "reviewed by staff. This tool never raises and sends nothing: it "
        "returns 'status': 'escalated' with the escalation receipt under "
        "'data'. After a successful hand-off, stop trying to message the "
        "patient and report the escalation."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "case_id": {
                "type": "string",
                "description": "The follow-up case identifier.",
            },
            "patient_id": {
                "type": "string",
                "description": "The patient identifier.",
            },
            "reason": {
                "type": "string",
                "description": "Short, specific explanation of why a human is needed.",
            },
            "details": {
                "type": "object",
                "description": (
                    "Optional structured context, e.g. the last error_code and "
                    "the channels already attempted."
                ),
            },
            "urgency": {
                "type": "string",
                "enum": ["low", "normal", "high"],
                "description": "How urgently staff should look at this.",
            },
        },
        "required": ["case_id", "patient_id", "reason"],
    },
}

#: All tool schemas keyed by tool name.
TOOL_SCHEMAS: Dict[str, Dict[str, Any]] = {
    schema["name"]: schema
    for schema in (
        _SEND_WHATSAPP_SCHEMA,
        _SEND_SMS_SCHEMA,
        _SEND_EMAIL_SCHEMA,
        _AWS_SEND_SMS_SCHEMA,
        _AWS_SEND_EMAIL_SCHEMA,
        _CANDIDATE_CHANNELS_SCHEMA,
        _LIST_TEMPLATES_SCHEMA,
        _ESCALATE_SCHEMA,
    )
}


def get_tool_schemas(names: Optional[Sequence[str]] = None) -> List[Dict[str, Any]]:
    """
    Return the tool definitions to hand to the Anthropic API.

    Args:
        names: Optional subset of tool names; defaults to every tool.

    Returns:
        A list of ``{"name", "description", "input_schema"}`` dictionaries,
        ordered as requested.
    """
    selected = list(names) if names else list(TOOL_NAMES)
    missing = [name for name in selected if name not in TOOL_SCHEMAS]
    if missing:
        raise KeyError(f"No schema defined for tool(s): {', '.join(missing)}")
    return [TOOL_SCHEMAS[name] for name in selected]


def tool_names() -> List[str]:
    """
    Return the tool names that have schemas.

    Returns:
        Sorted schema names.
    """
    return sorted(TOOL_SCHEMAS)


def validate_schema_coverage() -> List[str]:
    """
    Check that schemas and tool implementations expose the same tool names.

    Returns:
        A list of human-readable problems; empty when everything matches.
    """
    problems: List[str] = []
    missing = [name for name in TOOL_NAMES if name not in TOOL_SCHEMAS]
    extra = [name for name in TOOL_SCHEMAS if name not in TOOL_NAMES]
    if missing:
        problems.append(f"Tools without a schema: {', '.join(missing)}")
    if extra:
        problems.append(f"Schemas without a tool: {', '.join(extra)}")
    for name, schema in TOOL_SCHEMAS.items():
        if not schema.get("description"):
            problems.append(f"Schema '{name}' has no description")
        input_schema = schema.get("input_schema") or {}
        if input_schema.get("type") != "object":
            problems.append(f"Schema '{name}' input_schema must be an object")
    return problems
