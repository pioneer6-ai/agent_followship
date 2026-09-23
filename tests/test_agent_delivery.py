"""
Tests for the agent layer's delivery, fallback and decision behaviour.

The point of these tests is the claim the whole refactor exists to support: the
agent must *perceive* a failed send and decide what to do next, instead of
crashing or reporting success. Every test here therefore drives the real
orchestrator with an injected backend whose failures are scripted, and asserts on
what the agent did about them.

Nothing here touches the network or a provider: the delivery backends are local
doubles, and the LLM decision engine is driven by
:class:`tools.llm_agent.ScriptedModelClient`.
"""

from __future__ import annotations
import contextlib
import io
import os
import sys
from datetime import date, timedelta
from typing import Dict, List, Optional

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from agent.action_handlers import EscalationHandler  # noqa: E402
from agent.decision import (  # noqa: E402
    CLOSED_STATUSES, DECISION_TOOL, ActionDecision, DecisionContext,
    LlmDecisionEngine, RuleDecisionEngine,
)
from agent.delivery import (  # noqa: E402
    DeliveryBackend, NotificationOutcome, PrintDeliveryBackend,
    ToolkitDeliveryBackend, is_configured_for_live_sends,
)
from agent.notifications import (  # noqa: E402
    EmailChannel, PhoneCallChannel, SMSChannel, WhatsAppChannel,
    build_notification_channels,
)
from agent.orchestrator import FollowUpAgentOrchestrator  # noqa: E402
from core.actions import AgentAction  # noqa: E402
from core.config import ClinicPolicyConfig  # noqa: E402
from core.data_access import MockCalendarIntegration, MockPatientDataStore  # noqa: E402
from core.models import (  # noqa: E402
    CaseStatus, ContactChannel, FollowUpCase, PatientRecord, UrgencyLevel,
)
from tools.llm_agent import ScriptedModelClient  # noqa: E402

# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class ScriptedBackend(DeliveryBackend):
    """
    Backend that fails or succeeds according to a per-channel script.

    ``outcomes`` maps a channel to the outcomes returned for successive attempts
    on it; the last entry repeats. Recorded calls are available for assertions.
    """

    def __init__(self, outcomes: Optional[Dict[ContactChannel, List[NotificationOutcome]]] = None):
        self.outcomes = outcomes or {}
        self.calls: List[ContactChannel] = []
        self.deliveries: List[tuple] = []

    def deliver(
        self,
        channel: ContactChannel,
        recipient: str,
        body: str,
        subject: str = "",
    ) -> NotificationOutcome:
        """Return the scripted outcome for this channel."""
        self.calls.append(channel)
        self.deliveries.append((channel, recipient, subject, body))
        scripted = self.outcomes.get(channel)
        if not scripted:
            return NotificationOutcome(
                success=True, channel=channel, recipient=recipient, message_id="ok"
            )
        return scripted[0] if len(scripted) == 1 else scripted.pop(0)


def failure(
    channel: ContactChannel,
    code: str = "recipient_not_verified",
    *,
    retryable: bool = False,
    fallbacks: Optional[List[ContactChannel]] = None,
    hint: str = "try another channel",
) -> NotificationOutcome:
    """Build a failed outcome."""
    return NotificationOutcome(
        success=False,
        channel=channel,
        error_code=code,
        error_message=f"scripted {code}",
        retryable=retryable,
        suggested_fallbacks=fallbacks or [],
        hint=hint,
    )


def make_case(
    patient_id: str = "P1",
    preferred: ContactChannel = ContactChannel.SMS,
    contact_info: Optional[Dict[ContactChannel, str]] = None,
    status: CaseStatus = CaseStatus.PENDING,
    reminder_count: int = 0,
    last_contacted: Optional[date] = None,
    urgency: UrgencyLevel = UrgencyLevel.MEDIUM,
) -> FollowUpCase:
    """Build a case without going through the data store."""
    patient = PatientRecord(
        patient_id=patient_id,
        name=f"Patient {patient_id}",
        contact_info=(
            contact_info if contact_info is not None
            else {ContactChannel.SMS: "+15550001111"}
        ),
        preferred_channel=preferred,
        last_visit_date=date(2026, 1, 1),
        treatment_type="cleaning",
        recall_interval_days=180,
    )
    return FollowUpCase(
        patient=patient,
        days_overdue=30,
        urgency=urgency,
        reason="routine recall",
        status=status,
        reminder_count=reminder_count,
        last_contacted=last_contacted,
    )


def make_agent(
    backend: Optional[DeliveryBackend] = None,
    policy: Optional[ClinicPolicyConfig] = None,
    decision_engine=None,
) -> FollowUpAgentOrchestrator:
    """Build an orchestrator wired to an offline backend."""
    return FollowUpAgentOrchestrator(
        MockPatientDataStore(),
        MockCalendarIntegration(),
        policy or ClinicPolicyConfig(),
        delivery_backend=backend or ScriptedBackend(),
        decision_engine=decision_engine,
    )


class FakeToolResult:
    """Stand-in for a ``ToolResult`` returned by the tool layer."""

    def __init__(self, **kwargs):
        self.tool = kwargs.get("tool", "send_sms_message")
        self.success = kwargs.get("success", False)
        self.channel = kwargs.get("channel", "sms")
        self.recipient = kwargs.get("recipient", "+15550001111")
        self.message_id = kwargs.get("message_id")
        self.simulated = kwargs.get("simulated", False)
        self.error_code = kwargs.get("error_code")
        self.error_message = kwargs.get("error_message")
        self.provider_code = kwargs.get("provider_code")
        self.retryable = kwargs.get("retryable", False)
        self.suggested_fallback_channels = kwargs.get("suggested_fallback_channels", [])
        self.hint = kwargs.get("hint")


class FakeRegistry:
    """Registry that returns a canned result and records the call."""

    def __init__(self, result):
        self.result = result
        self.calls: List[tuple] = []

    def call(self, name, arguments):
        self.calls.append((name, arguments))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


# ---------------------------------------------------------------------------
# The failure the user cares about: an unverified recipient must not crash
# ---------------------------------------------------------------------------


class TestPerceivingSendFailure:
    """A failed send must be observed, and acted on, not swallowed."""

    def test_failed_send_falls_back_to_another_channel(self):
        """
        The scenario from the brief: the primary channel rejects the recipient,
        so the agent switches to a channel that works.
        """
        backend = ScriptedBackend(
            {
                ContactChannel.SMS: [failure(
                    ContactChannel.SMS,
                    fallbacks=[ContactChannel.WHATSAPP],
                )],
                ContactChannel.WHATSAPP: [],
            }
        )
        agent = make_agent(backend)
        case = make_case(
            contact_info={
                ContactChannel.SMS: "+15550001111",
                ContactChannel.WHATSAPP: "+15550001111",
            },
            preferred=ContactChannel.SMS,
        )

        decision = ActionDecision(AgentAction.SEND_REMINDER, "due for a reminder")
        agent._execute_action(case, decision, date(2026, 6, 1))

        assert backend.calls == [ContactChannel.SMS, ContactChannel.WHATSAPP]
        assert case.status is CaseStatus.MESSAGE_SENT
        assert case.reminder_count == 1
        assert any("failed via sms" in entry for entry in case.conversation_log)
        assert any("delivered via whatsapp" in entry for entry in case.conversation_log)

    def test_total_failure_escalates_instead_of_reporting_success(self):
        """
        Every channel failing must escalate. Before the refactor the cycle printed
        "✅ complete" while sending nothing, which is the bug being fixed.
        """
        backend = ScriptedBackend(
            {
                ContactChannel.SMS: [failure(ContactChannel.SMS)],
                ContactChannel.EMAIL: [failure(ContactChannel.EMAIL)],
            }
        )
        agent = make_agent(backend)
        case = make_case(
            contact_info={
                ContactChannel.SMS: "+15550001111",
                ContactChannel.EMAIL: "p@example.com",
            },
            preferred=ContactChannel.SMS,
        )

        decision = ActionDecision(AgentAction.SEND_REMINDER, "due for a reminder")
        agent._execute_action(case, decision, date(2026, 6, 1))

        assert backend.calls == [ContactChannel.SMS, ContactChannel.EMAIL]
        # Crucially, NOT message_sent, and NOT a silent success.
        assert case.status is CaseStatus.ESCALATED
        assert agent.escalation_handler.escalated_cases, "case was not escalated"
        assert agent.undelivered, "failure was not recorded for later cycles"

    def test_unsupported_channel_is_a_failure_the_agent_routes_around(self):
        """
        PHONE_CALL has no automated sender, so it must fall through to a channel
        that does. Uses the real toolkit backend so the ``unsupported_channel``
        outcome is genuine rather than scripted.
        """
        registry = FakeRegistry(
            FakeToolResult(success=True, channel="email", recipient="p@example.com")
        )
        agent = make_agent(ToolkitDeliveryBackend(registry))
        case = make_case(
            contact_info={
                ContactChannel.PHONE_CALL: "+15550001111",
                ContactChannel.EMAIL: "p@example.com",
            },
            preferred=ContactChannel.PHONE_CALL,
        )

        outcome = agent._deliver_with_fallback(case, "hello")

        assert outcome.success
        assert outcome.channel is ContactChannel.EMAIL
        assert registry.calls == [
            (
                "send_email_message",
                {
                    "recipient": "p@example.com",
                    "body": "hello",
                    "subject": "Dental Appointment Reminder",
                },
            )
        ], "the phone-call channel must not reach a tool"

    def test_preferred_channel_is_tried_first(self):
        """Fallback must not change which channel the patient prefers."""
        backend = ScriptedBackend()
        agent = make_agent(backend)
        case = make_case(
            contact_info={
                ContactChannel.EMAIL: "p@example.com",
                ContactChannel.SMS: "+15550001111",
            },
            preferred=ContactChannel.EMAIL,
        )

        agent._deliver_with_fallback(case, "hello")

        assert backend.calls[0] is ContactChannel.EMAIL

    def test_a_failure_on_one_channel_does_not_stop_the_next(self):
        """
        A non-retryable SMS rejection says nothing about the patient's email
        address, so the agent must still try it. Treating one channel's failure
        as proof about another is how a reachable patient gets abandoned.
        """
        backend = ScriptedBackend(
            {
                ContactChannel.SMS: [failure(ContactChannel.SMS, code="not_subscribed")],
                ContactChannel.EMAIL: [],
            }
        )
        agent = make_agent(backend)
        case = make_case(
            contact_info={
                ContactChannel.SMS: "+15550001111",
                ContactChannel.EMAIL: "p@example.com",
            }
        )

        outcome = agent._deliver_with_fallback(case, "hello")

        assert outcome.success
        assert backend.calls == [ContactChannel.SMS, ContactChannel.EMAIL]

    def test_a_channel_that_already_failed_is_not_retried(self):
        """The agent remembers failures across cycles within a run."""
        backend = ScriptedBackend(
            {
                ContactChannel.SMS: [failure(ContactChannel.SMS)],
                ContactChannel.EMAIL: [failure(ContactChannel.EMAIL)],
            }
        )
        agent = make_agent(backend)
        case = make_case(
            contact_info={
                ContactChannel.SMS: "+15550001111",
                ContactChannel.EMAIL: "p@example.com",
            }
        )

        agent._deliver_with_fallback(case, "one")
        backend.calls.clear()
        agent._deliver_with_fallback(case, "two")

        assert ContactChannel.EMAIL not in backend.calls

    def test_missing_contact_details_is_an_outcome_not_a_crash(self):
        """A patient with no contact details must not raise."""
        agent = make_agent(ScriptedBackend())
        case = make_case(contact_info={})

        outcome = agent._deliver_with_fallback(case, "hello")

        assert not outcome.success
        assert outcome.error_code == "missing_recipient"


# ---------------------------------------------------------------------------
# The dead safety valve: reminder progress must reach escalation
# ---------------------------------------------------------------------------


class TestReminderProgression:
    """A patient who never replies must eventually reach a human."""

    def test_reminders_keep_being_sent_until_the_budget_is_spent(self):
        """
        Regression test for the bug where ``MESSAGE_SENT`` excluded a case from
        ever being reconsidered, freezing ``reminder_count`` at 1.
        """
        policy = ClinicPolicyConfig(reminder_interval_days=0, max_reminders_before_escalation=3)
        backend = ScriptedBackend(
            {
                ContactChannel.SMS: [
                    NotificationOutcome(True, ContactChannel.SMS, message_id="m1")
                ]
            }
        )
        agent = make_agent(backend, policy=policy)
        case = make_case(preferred=ContactChannel.SMS)
        today = date(2026, 6, 1)

        for _ in range(3):
            decision = agent._decide_for_case(case, today)
            agent._execute_action(case, decision, today)

        assert case.reminder_count == 3
        assert case.status is CaseStatus.MESSAGE_SENT

    def test_escalation_becomes_reachable_after_the_budget(self):
        """
        ``max_reminders_before_escalation`` must actually be reachable. It was not:
        the case never re-entered the decision path, so the escalation branch was
        dead code and a non-responding patient was abandoned forever.
        """
        policy = ClinicPolicyConfig(reminder_interval_days=0, max_reminders_before_escalation=2)
        agent = make_agent(ScriptedBackend(), policy=policy)
        case = make_case(reminder_count=2, status=CaseStatus.MESSAGE_SENT)

        decision = agent._decide_for_case(case, date(2026, 6, 1))

        assert decision.action is AgentAction.ESCALATE_TO_STAFF

    def test_full_lifecycle_reaches_escalation_without_ever_replying(self):
        """End-to-end: reminders then escalation, with no patient response."""
        policy = ClinicPolicyConfig(reminder_interval_days=0, max_reminders_before_escalation=3)
        backend = ScriptedBackend()
        agent = make_agent(backend, policy=policy)
        case = make_case(preferred=ContactChannel.SMS)
        today = date(2026, 6, 1)

        actions = []
        for _ in range(5):
            decision = agent._decide_for_case(case, today)
            actions.append(decision.action)
            agent._execute_action(case, decision, today)

        assert actions[:3] == [AgentAction.SEND_REMINDER] * 3
        assert AgentAction.ESCALATE_TO_STAFF in actions[3:]
        assert agent.escalation_handler.escalated_cases

    def test_quiet_period_is_respected(self):
        """The agent must not spam: a recent contact suppresses the reminder."""
        policy = ClinicPolicyConfig(reminder_interval_days=7)
        agent = make_agent(ScriptedBackend(), policy=policy)
        today = date(2026, 6, 10)
        case = make_case(status=CaseStatus.MESSAGE_SENT, last_contacted=today - timedelta(days=2))

        decision = agent._decide_for_case(case, today)

        assert decision.action is AgentAction.DO_NOTHING

    @pytest.mark.parametrize("status", CLOSED_STATUSES)
    def test_closed_cases_are_never_contacted_again(self, status):
        """BOOKED / DECLINED / ESCALATED are terminal."""
        agent = make_agent(ScriptedBackend())
        case = make_case(status=status)

        assert agent._decide_for_case(case, date(2026, 6, 1)).action is AgentAction.DO_NOTHING


# ---------------------------------------------------------------------------
# Rule engine as guardrail
# ---------------------------------------------------------------------------


class TestRuleDecisionEngine:
    """The rules define what is permissible, not just what is preferred."""

    def test_permissible_set_shrinks_to_escalation_when_all_channels_failed(self):
        engine = RuleDecisionEngine(ClinicPolicyConfig())
        case = make_case(contact_info={ContactChannel.SMS: "+15550001111"})
        context = DecisionContext(
            case=case,
            today=date(2026, 6, 1),
            policy=ClinicPolicyConfig(),
            available_channels=[ContactChannel.SMS],
            exhausted_channels=[ContactChannel.SMS],
        )

        assert engine.permissible_actions(context) == [AgentAction.ESCALATE_TO_STAFF]

    def test_rationale_mentions_the_facts(self):
        engine = RuleDecisionEngine(ClinicPolicyConfig())
        case = make_case()
        decision = engine.decide(
            DecisionContext(
                case=case,
                today=date(2026, 6, 1),
                policy=ClinicPolicyConfig(),
                available_channels=[ContactChannel.SMS],
            )
        )

        assert decision.source == "rules"
        assert "30 days overdue" in decision.rationale

    def test_actions_are_always_non_empty(self):
        engine = RuleDecisionEngine(ClinicPolicyConfig())
        case = make_case()
        context = DecisionContext(
            case=case, today=date(2026, 6, 1), policy=ClinicPolicyConfig()
        )
        assert engine.permissible_actions(context)


# ---------------------------------------------------------------------------
# LLM decision engine
# ---------------------------------------------------------------------------


def tool_use_response(action: str, rationale: str = "because") -> Dict[str, object]:
    """Build an Anthropic-shaped response calling ``choose_next_action``."""
    return {
        "stop_reason": "tool_use",
        "content": [
            {
                "type": "tool_use",
                "name": DECISION_TOOL["name"],
                "input": {"action": action, "rationale": rationale},
            }
        ],
    }


def make_context(case: Optional[FollowUpCase] = None, **kwargs) -> DecisionContext:
    """Build a decision context for a fresh case."""
    policy = kwargs.pop("policy", ClinicPolicyConfig())
    case = case or make_case()
    return DecisionContext(
        case=case,
        today=kwargs.pop("today", date(2026, 6, 1)),
        policy=policy,
        available_channels=kwargs.pop("available_channels", [ContactChannel.SMS]),
        **kwargs,
    )


class TestLlmDecisionEngine:
    """The model may choose, but only within the rules' permissive set."""

    def test_without_a_client_it_behaves_like_the_rules(self):
        """An offline run must not depend on the model being reachable."""
        engine = LlmDecisionEngine(ClinicPolicyConfig())

        decision = engine.decide(make_context())

        assert decision.action is AgentAction.SEND_REMINDER
        assert decision.source == "llm-disabled"

    def test_a_permissible_choice_is_used(self):
        """Given a real choice, the model's answer is taken."""
        client = ScriptedModelClient(
            [tool_use_response("escalate_to_staff", "patient ignores reminders")]
        )
        engine = LlmDecisionEngine(ClinicPolicyConfig(), client=client)
        case = make_case(reminder_count=2, status=CaseStatus.MESSAGE_SENT)

        decision = engine.decide(make_context(case))

        assert decision.action is AgentAction.ESCALATE_TO_STAFF
        assert decision.source == "llm"
        assert "patient ignores reminders" in decision.rationale

    def test_an_impermissible_choice_is_overridden_by_the_rules(self):
        """The model cannot talk the agent into an action clinic policy forbids."""
        client = ScriptedModelClient(
            [tool_use_response("mark_declined", "patient probably gave up")]
        )
        engine = LlmDecisionEngine(ClinicPolicyConfig(), client=client)

        decision = engine.decide(make_context())

        assert decision.action is AgentAction.SEND_REMINDER
        assert decision.source == "llm-guardrail"
        assert engine.last_error and "not permitted" in engine.last_error

    def test_an_unknown_action_is_overridden(self):
        client = ScriptedModelClient([tool_use_response("delete_patient_record")])
        engine = LlmDecisionEngine(ClinicPolicyConfig(), client=client)

        decision = engine.decide(make_context())

        assert decision.source == "llm-guardrail"
        assert decision.action is AgentAction.SEND_REMINDER

    def test_a_response_without_a_tool_call_is_overridden(self):
        client = ScriptedModelClient(
            [{"stop_reason": "end_turn", "content": [{"type": "text", "text": "hmm"}]}]
        )
        engine = LlmDecisionEngine(ClinicPolicyConfig(), client=client)

        decision = engine.decide(make_context())

        assert decision.source == "llm-guardrail"

    def test_an_api_error_falls_back_to_the_rules(self):
        """A provider outage must degrade to rules, not break the cycle."""

        class Exploding:
            class messages:  # noqa: N801
                @staticmethod
                def create(**kwargs):
                    raise RuntimeError("model unavailable")

        engine = LlmDecisionEngine(ClinicPolicyConfig(), client=Exploding())

        decision = engine.decide(make_context())

        assert decision.action is AgentAction.SEND_REMINDER
        assert decision.source == "llm-error"
        assert "model unavailable" in decision.rationale

    def test_an_obvious_case_is_not_sent_to_the_model(self):
        """With one permissible action there is nothing to ask about."""
        client = ScriptedModelClient([tool_use_response("do_nothing")])
        engine = LlmDecisionEngine(ClinicPolicyConfig(), client=client)
        case = make_case(status=CaseStatus.BOOKED)

        decision = engine.decide(make_context(case))

        assert decision.action is AgentAction.DO_NOTHING
        assert not client.requests, "model was called for a case with no choice"

    def test_the_prompt_shows_only_permitted_actions(self):
        """The model must not be tempted by actions it cannot pick."""
        client = ScriptedModelClient([tool_use_response("send_reminder")])
        engine = LlmDecisionEngine(ClinicPolicyConfig(), client=client)

        engine.decide(make_context())

        prompt = client.requests[0]["messages"][0]["content"]
        assert "send_reminder" in prompt
        assert "confirm_booking" not in prompt

    def test_from_environment_without_a_key_is_rules_only(self):
        """No API key means rules only, with no exception raised."""
        engine = LlmDecisionEngine.from_environment(
            ClinicPolicyConfig(), api_key=None
        )
        assert engine.client is None or os.environ.get("ANTHROPIC_API_KEY")


# ---------------------------------------------------------------------------
# Backends and the live-send safety gate
# ---------------------------------------------------------------------------


class TestDeliveryBackends:
    """The offline backend must be the default, and live sends opt-in twice."""

    def test_print_backend_is_the_default(self):
        channels = build_notification_channels()
        for channel in channels.values():
            assert isinstance(channel.backend, PrintDeliveryBackend)

    def test_live_sends_need_both_switches(self, monkeypatch):
        """
        A populated ``.env`` must not be enough to start texting patients -- that
        is what makes running the demo safe.
        """
        monkeypatch.delenv("AGENT_LIVE_SENDS", raising=False)
        monkeypatch.setenv("MESSAGING_DRY_RUN", "0")
        assert not is_configured_for_live_sends()

        monkeypatch.setenv("AGENT_LIVE_SENDS", "1")
        monkeypatch.setenv("MESSAGING_DRY_RUN", "1")
        assert not is_configured_for_live_sends()

        monkeypatch.setenv("MESSAGING_DRY_RUN", "0")
        assert is_configured_for_live_sends()

    def test_print_backend_handles_empty_body_and_recipient(self):
        backend = PrintDeliveryBackend()
        assert backend.deliver(ContactChannel.SMS, "", "hi").error_code == "missing_recipient"
        outcome = backend.deliver(ContactChannel.SMS, "+15550001111", "")
        assert outcome.error_code == "invalid_request"


class TestToolkitDeliveryBackend:
    """The real path must translate the tool contract without losing detail."""

    def test_failure_metadata_survives_translation(self):
        registry = FakeRegistry(
            FakeToolResult(
                success=False,
                error_code="recipient_not_verified",
                error_message="not verified",
                provider_code="131030",
                retryable=False,
                suggested_fallback_channels=["whatsapp"],
                hint="use another channel",
            )
        )
        outcome = ToolkitDeliveryBackend(registry).deliver(
            ContactChannel.SMS, "+15550001111", "hi"
        )

        assert not outcome.success
        assert outcome.error_code == "recipient_not_verified"
        assert outcome.provider_code == "131030"
        assert outcome.suggested_fallbacks == [ContactChannel.WHATSAPP]
        assert outcome.hint == "use another channel"

    def test_success_carries_the_message_id(self):
        registry = FakeRegistry(
            FakeToolResult(success=True, message_id="SM123", channel="sms")
        )
        outcome = ToolkitDeliveryBackend(registry).deliver(
            ContactChannel.SMS, "+15550001111", "hi"
        )

        assert outcome.success and outcome.message_id == "SM123"
        assert registry.calls[0][0] == "send_sms_message"

    def test_email_sends_the_subject(self):
        registry = FakeRegistry(FakeToolResult(success=True, channel="email"))
        ToolkitDeliveryBackend(registry).deliver(
            ContactChannel.EMAIL, "p@example.com", "body", subject="Reminder"
        )

        assert registry.calls[0][0] == "send_email_message"
        assert registry.calls[0][1]["subject"] == "Reminder"

    def test_phone_call_is_reported_as_unsupported(self):
        """No voice sender exists; the agent must be told, not left guessing."""
        registry = FakeRegistry(FakeToolResult(success=True))
        outcome = ToolkitDeliveryBackend(registry).deliver(
            ContactChannel.PHONE_CALL, "+15550001111", "hi"
        )

        assert outcome.error_code == "unsupported_channel"
        assert not registry.calls, "a tool was called for an unsupported channel"
        assert ContactChannel.SMS in outcome.suggested_fallbacks

    def test_an_exploding_registry_becomes_an_outcome(self):
        """Even an unexpected tool failure must not break the cycle."""
        outcome = ToolkitDeliveryBackend(FakeRegistry(RuntimeError("boom"))).deliver(
            ContactChannel.SMS, "+15550001111", "hi"
        )

        assert not outcome.success
        assert outcome.error_code == "unexpected"
        assert "boom" in outcome.error_message


# ---------------------------------------------------------------------------
# Channel plumbing
# ---------------------------------------------------------------------------


class TestChannelWiring:
    """Channels must pass the right address and channel to the backend."""

    def test_sms_channel_uses_its_own_address(self):
        backend = ScriptedBackend()
        channel = SMSChannel(backend=backend)
        case = make_case(
            contact_info={
                ContactChannel.SMS: "+15550001111",
                ContactChannel.EMAIL: "p@example.com",
            }
        )

        outcome = channel.send(case.patient, "hi")

        assert outcome.success
        assert backend.calls == [ContactChannel.SMS]

    def test_channel_falls_back_to_the_preferred_address(self):
        """
        A patient whose only number is recorded under PHONE_CALL is still
        reachable by SMS, so the channel must not report "no number".
        """
        backend = ScriptedBackend()
        channel = SMSChannel(backend=backend)
        case = make_case(
            contact_info={ContactChannel.PHONE_CALL: "+15550001111"},
            preferred=ContactChannel.PHONE_CALL,
        )

        outcome = channel.send(case.patient, "hi")

        assert outcome.success, outcome.error_code

    def test_email_channel_supplies_a_subject(self):
        backend = ScriptedBackend()
        channel = EmailChannel(backend=backend)
        case = make_case(contact_info={ContactChannel.EMAIL: "p@example.com"})

        outcome = channel.send(case.patient, "hi")

        assert outcome.success

    def test_whatsapp_and_phone_report_missing_details(self):
        backend = ScriptedBackend()
        for channel, expected in (
            (WhatsAppChannel(backend=backend), ContactChannel.WHATSAPP.value),
            (PhoneCallChannel(backend=backend), ContactChannel.PHONE_CALL.value),
        ):
            outcome = channel.send(make_case(contact_info={}).patient, "hi")
            assert not outcome.success
            assert expected == outcome.channel.value

    def test_every_channel_reports_the_channel_it_used(self):
        backend = ScriptedBackend()
        channels = build_notification_channels(backend)
        for channel_type, channel in channels.items():
            assert channel.get_channel_type() is channel_type
            assert channel.backend is backend


# ---------------------------------------------------------------------------
# Multi-cycle state: a patient who never replies must not be quietly forgotten
# ---------------------------------------------------------------------------
class TestStateSurvivesAcrossCycles:
    """
    ``run_daily_cycle`` rebuilds every case from the data store, so the freshly
    perceived object knows the new ``days_overdue`` but has ``reminder_count == 0``.
    If that object simply replaces the tracked one, the reminder counter resets
    every single day, the escalation cap becomes unreachable, and a patient who
    never answers is abandoned while the summary still reports success.

    These tests run real cycles to prove the progress is carried forward.
    """

    @staticmethod
    def make_populated(backend, policy=None):
        store, calendar = MockPatientDataStore(), MockCalendarIntegration()
        store.add_patient(
            PatientRecord(
                patient_id="P900",
                name="Quiet Patient",
                contact_info={ContactChannel.SMS: "+15550009999"},
                preferred_channel=ContactChannel.SMS,
                last_visit_date=date.today() - timedelta(days=40),
                treatment_type="cleaning",
                recall_interval_days=1,
                language="en",
            )
        )
        agent = FollowUpAgentOrchestrator(
            store,
            calendar,
            policy or ClinicPolicyConfig(),
            delivery_backend=backend,
            decision_engine=RuleDecisionEngine(policy or ClinicPolicyConfig()),
        )
        return agent, store

    def test_reminder_count_advances_across_cycles(self):
        backend = ScriptedBackend()
        agent, _ = self.make_populated(backend)
        today = date.today()

        counts = []
        for week in range(5):
            cases = agent.run_daily_cycle(today + timedelta(days=7 * week))
            counts.append(cases[0].reminder_count)

        assert counts == [1, 2, 3, 3, 3], (
            f"reminder_count reset instead of accumulating: {counts}"
        )

    def test_never_responding_patient_is_eventually_escalated(self):
        """
        The safety valve. Three reminders is the documented cap, so by the third
        week the case must be with a human -- not still silently open.
        """
        backend = ScriptedBackend()
        agent, _ = self.make_populated(backend)
        today = date.today()

        for week in range(5):
            cases = agent.run_daily_cycle(today + timedelta(days=7 * week))

        case = cases[0]
        assert case.status is CaseStatus.ESCALATED
        assert case.reminder_count == 3
        assert agent.active_cases["P900"].status is CaseStatus.ESCALATED

    def test_critical_urgency_goes_to_a_human_after_one_reminder(self):
        """
        ``ConversationManager.should_escalate`` escalates a critical case after a
        single unanswered reminder. That rule was unreachable while
        ``reminder_count`` was always zero at decision time -- the counter was
        reset before the check could ever see a non-zero value.
        """
        backend = ScriptedBackend()
        agent, _ = self.make_populated(backend)
        # Urgency is scored by the rule engine, so pin it to critical here.
        agent.urgency_scorer.score = lambda case: UrgencyLevel.CRITICAL
        today = date.today()

        agent.run_daily_cycle(today)
        cases = agent.run_daily_cycle(today + timedelta(days=7))

        assert cases[0].reminder_count == 1
        assert cases[0].status is CaseStatus.ESCALATED

    def test_carry_forward_keeps_history_but_adopts_new_facts(self):
        previous = make_case()
        previous.status = CaseStatus.MESSAGE_SENT
        previous.reminder_count = 2
        previous.last_contacted = date(2024, 1, 1)
        previous.add_to_log("earlier conversation")

        current = make_case(urgency=UrgencyLevel.CRITICAL)
        current.days_overdue = 99
        FollowUpAgentOrchestrator._carry_forward(previous, current)

        assert current.reminder_count == 2
        assert current.status is CaseStatus.MESSAGE_SENT
        assert current.last_contacted == date(2024, 1, 1)
        assert "earlier conversation" in current.conversation_log
        # The new perception must win on externally-observed facts.
        assert current.days_overdue == 99
        # ...and the copy must not alias the previous case's list.
        current.add_to_log("new")
        assert "new" not in previous.conversation_log


# ---------------------------------------------------------------------------
# Escalation must actually reach a human
# ---------------------------------------------------------------------------
class TestEscalationAlertsStaff:
    """
    Recording an escalation in a list nobody reads is not escalation. When a
    staff address is configured, the agent must email it -- through the same
    backend and the same dry-run gate as patient messages.
    """

    def test_escalation_emails_the_configured_staff_address(self):
        backend = ScriptedBackend()
        agent, _ = TestStateSurvivesAcrossCycles.make_populated(backend)
        agent.escalation_handler = EscalationHandler(
            alert_email="staff@clinic.example",
            notifier=agent._alert_staff,
        )
        agent.escalation_email = "staff@clinic.example"
        agent.urgency_scorer.score = lambda case: UrgencyLevel.CRITICAL
        today = date.today()

        agent.run_daily_cycle(today)
        agent.run_daily_cycle(today + timedelta(days=7))

        alerts = [
            (recipient, subject, body)
            for channel, recipient, subject, body in backend.deliveries
            if channel is ContactChannel.EMAIL and recipient == "staff@clinic.example"
        ]
        assert alerts, backend.deliveries
        _recipient, subject, body = alerts[0]
        assert "Escalation" in subject and "Quiet Patient" in subject
        assert "A patient follow-up case needs human attention." in body

    def test_a_failing_alert_does_not_abort_the_cycle(self):
        def explode(_escalation):
            raise RuntimeError("smtp down")

        handler = EscalationHandler(alert_email="staff@clinic.example", notifier=explode)
        case = make_case(urgency=UrgencyLevel.CRITICAL)

        handler.escalate(case, "unreachable", priority="high")

        assert case.status is CaseStatus.ESCALATED
        assert len(handler.get_escalated_cases()) == 1
        assert any("alert FAILED" in entry for entry in case.conversation_log)

    def test_no_alert_is_sent_when_no_address_is_configured(self):
        backend = ScriptedBackend()
        agent, _ = TestStateSurvivesAcrossCycles.make_populated(backend)
        agent.urgency_scorer.score = lambda case: UrgencyLevel.CRITICAL
        today = date.today()

        agent.run_daily_cycle(today)
        agent.run_daily_cycle(today + timedelta(days=7))

        assert agent.escalation_handler.notifier is None


# ---------------------------------------------------------------------------
# Configuration the operator can actually reach
# ---------------------------------------------------------------------------
class TestEnvironmentWiring:
    """The documented switches must be the switches that are actually read."""

    def test_decision_model_comes_from_the_environment(self, monkeypatch):
        from agent.decision import DEFAULT_LLM_MODEL

        monkeypatch.delenv("AGENT_DECISION_MODEL", raising=False)
        engine = LlmDecisionEngine.from_environment(ClinicPolicyConfig())
        assert engine.model == DEFAULT_LLM_MODEL

        monkeypatch.setenv("AGENT_DECISION_MODEL", "claude-opus-4")
        engine = LlmDecisionEngine.from_environment(ClinicPolicyConfig())
        assert engine.model == "claude-opus-4"

    def test_escalation_email_comes_from_the_environment(self, monkeypatch):
        monkeypatch.setenv("AGENT_ESCALATION_EMAIL", "staff@clinic.example")
        agent = make_agent()
        assert agent.escalation_email == "staff@clinic.example"
        assert agent.escalation_handler.notifier is not None

    def test_no_staff_address_means_no_notifier(self, monkeypatch):
        monkeypatch.delenv("AGENT_ESCALATION_EMAIL", raising=False)
        agent = make_agent()
        assert agent.escalation_email is None
        assert agent.escalation_handler.notifier is None

    def test_with_llm_decisions_degrades_to_the_rules_offline(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        agent = FollowUpAgentOrchestrator.with_llm_decisions(
            MockPatientDataStore(), MockCalendarIntegration()
        )
        # Without a key the LLM engine still exists but has no client, so it
        # delegates -- the run must behave exactly like rules-only.
        assert isinstance(agent.decision_engine, LlmDecisionEngine)
        assert agent.decision_engine.client is None
        assert isinstance(agent.decision_engine.inner, RuleDecisionEngine)


class TestDecisionSourceIsAudited:
    """
    ``ActionDecision.source`` is only useful if it survives into the audit trail.
    A reviewer must be able to tell a model's judgement from the rules' default
    without reading the code.
    """

    def test_audit_entry_records_which_engine_decided(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        backend = ScriptedBackend()
        agent, _ = TestStateSurvivesAcrossCycles.make_populated(backend)

        with contextlib.redirect_stdout(io.StringIO()):
            agent.run_daily_cycle(date.today())

        decisions = [
            entry
            for entry in agent.audit_logger.log_entries
            if entry["event_type"] == "agent_decision"
        ]
        assert decisions, "no decisions were audited"
        context = decisions[0]["additional_context"]
        assert context["decision_source"] == "rules"
        assert isinstance(context["alternatives_considered"], list)
