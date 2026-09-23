"""
Offline proof that a *failed* send becomes an *informed decision*.

Run it with::

    python -m tools.demo_tool_use

No API key, no network and no ``anthropic`` package are required: the model is
a :class:`~tools.llm_agent.ScriptedModelClient` whose scripted steps inspect the
real ``tool_result`` payloads, while the tools, the providers, the error
classification and the Anthropic tool-use loop are all the production code
paths. Only the two network edges (HTTP and SMTP) are doubled.

Three scenarios are reproduced:

====================  ==================================================
A  whatsapp -> sms    Meta replies 131030 "recipient not in allowed list".
                      The agent perceives ``recipient_not_verified``,
                      asks for alternatives and delivers over SMS.
B  whatsapp -> ...    WhatsApp is unverified, SMS returns 21610 (opted
   -> escalate        out). No channel is left, so the agent escalates to
                      staff instead of crashing or silently giving up.
C  whatsapp -> sms    SMS is rate limited (retryable, no channel
   -> email           suggestion), so the agent reasons its way to email
                      and delivers over SMTP.
====================  ==================================================
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence
import json
import os
import sys

# Allow `python tools/demo_tool_use.py` as well as `python -m tools.demo_tool_use`
# by putting the repository root on the import path before `tools` is imported.
if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.config import MessagingConfig
from tools.llm_agent import (
    AgentRun,
    ScriptedModelClient,
    ToolUseAgent,
    last_tool_result_payload,
)
from tools.messaging import CHANNEL_ORDER, EscalationLog, build_tool_registry
from tools.transport import FakeSmtpConnection, FakeTransport

CLINIC_PHONE = "+15550002222"
CLINIC_EMAIL = "frontdesk@brightsmile.example"

#: Fake credentials: enough for every provider to consider itself configured,
#: so the real request-building and error-classification paths execute.
#: ``MESSAGING_DRY_RUN=0`` is what makes those paths actually run; without it
#: every send would be simulated and the failure scenarios prove nothing.
DEMO_ENV: Dict[str, str] = {
    "MESSAGING_DRY_RUN": "0",
    "META_WHATSAPP_ACCESS_TOKEN": "demo-token",
    "META_WHATSAPP_PHONE_NUMBER_ID": "100000000000001",
    "TWILIO_ACCOUNT_SID": "ACdemodemodemodemodemodemodemo",
    "TWILIO_AUTH_TOKEN": "demo-auth-token",
    "TWILIO_WHATSAPP_FROM": "whatsapp:+14155238886",
    "TWILIO_SMS_FROM": "+14155238886",
    "SMTP_HOST": "smtp.example.com",
    "SMTP_PORT": "587",
    "SMTP_USERNAME": "frontdesk@brightsmile.example",
    "SMTP_PASSWORD": "demo-smtp-password",
    "EMAIL_FROM": CLINIC_EMAIL,
}


@dataclass
class DemoPatient:
    """The minimal patient context the messaging agent needs."""

    patient_id: str
    case_id: str
    name: str
    phone: str
    email: str
    treatment: str
    preferred_channel: str = "whatsapp"
    days_overdue: int = 21


def _tool_use(name: str, arguments: Dict[str, Any], call_id: str) -> Dict[str, Any]:
    """Build a one-step scripted assistant response that requests a tool."""
    return {
        "stop_reason": "tool_use",
        "content": [
            {"type": "tool_use", "id": call_id, "name": name, "input": arguments}
        ],
    }


def _final(text: str) -> Dict[str, Any]:
    """Build a scripted final assistant answer."""
    return {"stop_reason": "end_turn", "content": [{"type": "text", "text": text}]}


def _args_for(channel: str, patient: DemoPatient, params: Sequence[str]) -> Dict[str, Any]:
    """Build the tool arguments for a channel."""
    if channel == "whatsapp":
        return {
            "recipient": patient.phone,
            "template": "appointment_reminder",
            "params": list(params),
        }
    if channel == "sms":
        return {
            "recipient": patient.phone,
            "body": (
                f"Hello {patient.name}, our clinic would like to book your "
                f"{patient.treatment} follow-up. Call {CLINIC_PHONE} or reply "
                "to this message."
            ),
        }
    return {
        "recipient": patient.email,
        "subject": f"Your {patient.treatment} follow-up appointment",
        "body": (
            f"Dear {patient.name},\n\nOur records show your {patient.treatment} "
            f"follow-up is {patient.days_overdue} days overdue. Please reply to "
            f"this email or call {CLINIC_PHONE} so we can arrange a time.\n\n"
            "Kind regards,\nBright Smile Clinic"
        ),
        "template": "appointment_reminder",
        "params": list(params),
    }


def make_reactive_policy(patient: DemoPatient) -> Any:
    """
    Build a scripted "model" that branches on the real tool results.

    This stands in for Claude: it is given exactly the transcript the API would
    receive, reads the last ``tool_result``, and applies the same escalation
    logic a well-prompted model should apply.

    Args:
        patient: The patient being contacted.

    Returns:
        A callable suitable for :class:`ScriptedModelClient`.
    """
    attempted: List[str] = []
    asked_for_options = False
    params = [patient.name, patient.treatment, CLINIC_PHONE]

    def decide(messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        nonlocal asked_for_options

        payload = last_tool_result_payload(messages)

        if payload is None:
            # First decision: the patient prefers WhatsApp, so try it first with
            # an approved template (a business-initiated message).
            attempted.append("whatsapp")
            return _tool_use(
                "send_whatsapp_message", _args_for("whatsapp", patient, params), "t1"
            )

        status = payload.get("status")

        # --- Terminal: a human now owns the case. ---
        if status == "escalated":
            receipt = payload.get("data") or {}
            return _final(
                f"Escalated case {patient.case_id} for {patient.patient_id}: "
                f"{receipt.get('escalation_id')}. No further automated contact "
                "attempted."
            )

        # --- Terminal: a real delivery went out. ---
        if status == "sent":
            channel = payload.get("channel")
            delivered = "simulated" if payload.get("simulated") else "delivered"
            return _final(
                f"Reminder for {patient.patient_id} {delivered} via {channel} "
                f"(message_id={payload.get('message_id')})."
            )

        # --- The decision-support tool answered a question ('status': 'ok'). ---
        if status == "ok":
            asked_for_options = False
            order = (payload.get("data") or {}).get("fallback_order") or []
            target = next(
                (channel for channel in order if channel not in attempted), None
            )
            if target is None:
                return _escalate(
                    patient, attempted, "no channel left in fallback_order"
                )
            attempted.append(target)
            return _tool_use(
                f"send_{target}_message",
                _args_for(target, patient, params),
                f"t{len(attempted) + 1}",
            )

        # --- A send just failed. Perceive the failure, then decide. ---
        code = payload.get("error_code")
        failed_channel = payload.get("channel")
        suggestions = [
            channel
            for channel in payload.get("suggested_fallback_channels", [])
            if channel not in attempted
        ]

        # Ask the decision-support tool once, so the reasoning is explicit.
        if not asked_for_options and suggestions:
            asked_for_options = True
            return _tool_use(
                "get_candidate_send_channels",
                {
                    "preferred_channel": patient.preferred_channel,
                    "failed_channel": failed_channel,
                    "error_code": code,
                },
                "t2",
            )

        next_channel: Optional[str] = None
        if suggestions:
            next_channel = suggestions[0]
        elif payload.get("retryable"):
            # No channel was suggested, but the failure is transient: try any
            # other channel we have not used yet.
            next_channel = next(
                (
                    channel
                    for channel in CHANNEL_ORDER
                    if channel not in attempted and channel != failed_channel
                ),
                None,
            )

        if next_channel is None:
            return _escalate(
                patient,
                attempted,
                f"last error {code} on {failed_channel}",
                last_error_code=code,
            )

        attempted.append(next_channel)
        return _tool_use(
            f"send_{next_channel}_message",
            _args_for(next_channel, patient, params),
            f"t{len(attempted) + 1}",
        )

    return decide


def _escalate(
    patient: DemoPatient,
    attempted: List[str],
    reason: str,
    last_error_code: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Build the scripted "hand this to a human" tool call.

    Args:
        patient: The patient that could not be reached.
        attempted: Channels already tried.
        reason: Short explanation recorded on the escalation.
        last_error_code: Normalized code of the last failure, for the record.

    Returns:
        A scripted ``tool_use`` response for ``escalate_to_staff``.
    """
    details: Dict[str, Any] = {
        "attempted_channels": list(attempted),
        "preferred_channel": patient.preferred_channel,
    }
    if last_error_code:
        details["last_error_code"] = last_error_code
    return _tool_use(
        "escalate_to_staff",
        {
            "case_id": patient.case_id,
            "patient_id": patient.patient_id,
            "reason": f"No messaging channel could reach the patient ({reason})",
            "details": details,
            "urgency": "high" if patient.days_overdue >= 30 else "normal",
        },
        f"t{len(attempted) + 2}",
    )


def _build_agent(
    transport: FakeTransport,
    smtp: Optional[FakeSmtpConnection],
    policy: Any,
) -> tuple:
    """
    Build a registry + agent wired to the offline doubles.

    Args:
        transport: Scripted HTTP responses for the WhatsApp/SMS providers.
        smtp: Scripted SMTP connection for the email provider.
        policy: The scripted model policy.

    Returns:
        ``(registry, run_agent_callable, escalation_log)``.
    """
    escalation_log = EscalationLog()
    registry = build_tool_registry(
        MessagingConfig.from_env(DEMO_ENV),
        transport=transport,
        smtp_connection_factory=(lambda: smtp) if smtp is not None else None,
        escalation_log=escalation_log,
    )
    agent = ToolUseAgent(registry, client=ScriptedModelClient([policy]))
    return registry, agent, escalation_log


def _tool_use_blocks(run: AgentRun) -> List[Dict[str, Any]]:
    """
    Extract the ``tool_use`` blocks the model requested, in order.

    Args:
        run: The completed agent run.

    Returns:
        The requested tool_use blocks.
    """
    blocks: List[Dict[str, Any]] = []
    for message in run.transcript:
        if message.get("role") != "assistant":
            continue
        content = message.get("content")
        if isinstance(content, dict):
            content = [content]
        for block in content or []:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                blocks.append(block)
    return blocks


def _print_run(title: str, explanation: str, run: AgentRun, escalation_log: Any) -> None:
    """Print a readable transcript of one scenario."""
    print("=" * 78)
    print(title)
    print("=" * 78)
    print(explanation)
    print()

    calls = _tool_use_blocks(run)

    for index, result in enumerate(run.tool_results, start=1):
        call = calls[index - 1] if index - 1 < len(calls) else None
        if call:
            print(f"[{index}] DECIDE   {call['name']}({_brief(call['input'])})")
        if result.success:
            detail = f"message_id={result.message_id}" if result.message_id else ""
            if result.data and result.data.get("fallback_order") is not None:
                detail = f"fallback_order={result.data['fallback_order']}"
            elif result.data and result.data.get("escalation_id"):
                detail = f"escalation_id={result.data['escalation_id']}"
            print(
                f"    OBSERVE  {'OK  ' if not result.simulated else 'DRYRUN'} "
                f"{result.channel}: status={result.status} {detail}".rstrip()
            )
        else:
            print(
                f"    OBSERVE  FAIL {result.channel}: "
                f"error_code={_code(result.error_code)} "
                f"provider_code={result.provider_code} retryable={result.retryable}"
            )
            if result.suggested_fallback_channels:
                print(
                    f"             suggested_fallback_channels="
                    f"{result.suggested_fallback_channels}"
                )
            if result.hint:
                print(f"             hint: {_wrap(result.hint)}")

    print()
    print(f"FINAL ANSWER: {run.text}")
    print(f"turns={run.iterations} tool_calls={len(run.tool_results)}")
    if escalation_log.records:
        print("ESCALATIONS:")
        for record in escalation_log.records:
            print(f"  - {json.dumps(record.to_dict(), ensure_ascii=False)}")
    print()


def _code(code: Any) -> str:
    """Render an error code for display."""
    return code.value if hasattr(code, "value") else str(code)


def _brief(arguments: Dict[str, Any]) -> str:
    """Render tool arguments compactly."""
    parts = []
    for key, value in arguments.items():
        text = str(value)
        if len(text) > 42:
            text = text[:39] + "..."
        parts.append(f"{key}={text}")
    return ", ".join(parts)


def _wrap(text: str, width: int = 72, indent: int = 21) -> str:
    """Wrap a hint across lines so the transcript stays readable."""
    words = str(text).split()
    lines: List[str] = []
    current = ""
    for word in words:
        if len(current) + len(word) + 1 > width:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(current)
    return ("\n" + " " * indent).join(lines)


def main() -> int:
    """
    Run the three scenarios.

    Returns:
        ``0`` when every scenario behaved as required, ``1`` otherwise.
    """
    print()
    print("MESSAGING TOOL LAYER - offline tool-use demonstration")
    print("Model: ScriptedModelClient (stands in for Claude)")
    print("Edge doubles: FakeTransport (HTTP), FakeSmtpConnection (SMTP)")
    print()

    patient_a = DemoPatient(
        patient_id="P001",
        case_id="CASE-P001-2026-09",
        name="Alex Chen",
        phone="+15550001111",
        email="alex.chen@example.com",
        treatment="dental cleaning",
        preferred_channel="whatsapp",
        days_overdue=21,
    )

    # ---------------------------------------------------------------- A
    transport_a = FakeTransport(
        [
            {
                "status_code": 400,
                "body": {
                    "error": {
                        "message": (
                            "(#131030) Recipient phone number not in allowed list"
                        ),
                        "code": 131030,
                        "type": "OAuthException",
                    }
                },
            },
            {"status_code": 201, "body": {"sid": "SM9f2a1c7d", "status": "queued"}},
        ]
    )
    registry_a, agent_a, escalations_a = _build_agent(
        transport_a, None, make_reactive_policy(patient_a)
    )
    run_a = agent_a.run(
        "Patient P001 (Alex Chen) is 21 days overdue for a dental cleaning and "
        "prefers WhatsApp. Remind them of their follow-up appointment."
    )
    _print_run(
        "SCENARIO A: WhatsApp 'recipient not verified' -> automatic fallback",
        "Meta rejects the WhatsApp send with code 131030. The agent must not "
        "crash and must not blindly retry WhatsApp.",
        run_a,
        escalations_a,
    )

    checks = []
    first = run_a.tool_results[0] if run_a.tool_results else None
    checks.append(
        ("A: first attempt failed with recipient_not_verified",
         bool(first and not first.success and _code(first.error_code) == "recipient_not_verified"))
    )
    checks.append(
        ("A: fallback suggestions excluded the failed channel",
         bool(first and "whatsapp" not in first.suggested_fallback_channels))
    )
    checks.append(
        ("A: reminder delivered on sms after the failure",
         any(r.success and r.channel == "sms" for r in run_a.tool_results))
    )
    checks.append(("A: no escalation needed", not run_a.escalated))
    checks.append(("A: loop never raised", True))

    # ---------------------------------------------------------------- B
    patient_b = DemoPatient(
        patient_id="P002",
        case_id="CASE-P002-2026-09",
        name="Bella Ortiz",
        phone="+15550003333",
        email="bella.ortiz@example.com",
        treatment="root canal review",
        preferred_channel="whatsapp",
        days_overdue=45,
    )
    transport_b = FakeTransport(
        [
            {
                "status_code": 400,
                "body": {
                    "error": {
                        "message": "(#131030) Recipient phone number not in allowed list",
                        "code": 131030,
                    }
                },
            },
            {
                "status_code": 400,
                "body": {
                    "code": 21610,
                    "message": "Attempt to send to unsubscribed recipient",
                },
            },
        ]
    )
    registry_b, agent_b, escalations_b = _build_agent(
        transport_b, None, make_reactive_policy(patient_b)
    )
    run_b = agent_b.run(
        "Patient P002 (Bella Ortiz) is 45 days overdue for a root canal review. "
        "Reach them however you can."
    )
    _print_run(
        "SCENARIO B: every channel fails -> escalate instead of crashing",
        "WhatsApp is not verified and SMS was unsubscribed (21610, "
        "non-retryable with no fallback). The agent must hand the case to a "
        "human.",
        run_b,
        escalations_b,
    )

    codes = [_code(r.error_code) for r in run_b.failures]
    checks.append(("B: saw recipient_not_verified then opted_out",
                   "recipient_not_verified" in codes and "opted_out" in codes))
    checks.append(("B: escalated to staff", run_b.escalated))
    escalation_details = (
        escalations_b.records[0].details if escalations_b.records else {}
    )
    checks.append(
        (
            "B: escalation recorded with evidence",
            escalation_details.get("last_error_code") == "opted_out",
        )
    )

    # ---------------------------------------------------------------- C
    patient_c = DemoPatient(
        patient_id="P003",
        case_id="CASE-P003-2026-09",
        name="Dana Meyer",
        phone="+15550004444",
        email="dana.meyer@example.com",
        treatment="wisdom tooth check",
        preferred_channel="whatsapp",
        days_overdue=14,
    )
    transport_c = FakeTransport(
        [
            {
                "status_code": 400,
                "body": {
                    "error": {
                        "message": "(#131030) Recipient phone number not in allowed list",
                        "code": 131030,
                    }
                },
            },
            {
                "status_code": 429,
                "body": {
                    "code": 20429,
                    "message": "Too many requests",
                },
            },
        ]
    )
    smtp_c = FakeSmtpConnection()
    registry_c, agent_c, escalations_c = _build_agent(
        transport_c, smtp_c, make_reactive_policy(patient_c)
    )
    run_c = agent_c.run(
        "Patient P003 (Dana Meyer) is 14 days overdue for a wisdom tooth check."
    )
    _print_run(
        "SCENARIO C: retryable rate limit on SMS -> reason through to email",
        "WhatsApp is unverified and SMS is rate limited (retryable, but the "
        "error suggests no specific channel). The agent picks email itself and "
        "delivers over SMTP.",
        run_c,
        escalations_c,
    )

    codes_c = [_code(r.error_code) for r in run_c.failures]
    checks.append(("C: rate_limited observed on sms", "rate_limited" in codes_c))
    checks.append(("C: smtp hand-off happened", bool(smtp_c.sent)))
    checks.append(("C: email subject set by the model",
                   bool(smtp_c.sent and "wisdom tooth check" in str(smtp_c.sent[0]["Subject"]))))

    # ---------------------------------------------------------------- summary
    print("=" * 78)
    print("VERIFICATION")
    print("=" * 78)
    failed = 0
    for label, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
        if not ok:
            failed += 1

    print()
    print(f"attempts recorded in the audit trail: {len(registry_a.history)} "
          "(scenario A)")
    for entry in registry_a.history:
        print("  " + json.dumps(entry, ensure_ascii=False))

    print()
    print(f"{len(checks) - failed}/{len(checks)} checks passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
