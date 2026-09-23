"""
Decision engines: how the agent chooses what to do about a case.

The agent's decision used to be a hardcoded ``if/elif`` cascade buried inside the
orchestrator. That is a perfectly good *policy* but a poor *decision engine*: it
cannot take context into account beyond the handful of fields it tests, and it
cannot explain itself in the patient's own terms.

This module separates the two concerns:

* :class:`RuleDecisionEngine` keeps the clinic's deterministic policy. It also
  produces the set of actions that are *permissible* for a case, which is what
  makes the rules a **guardrail** rather than a competitor.
* :class:`LlmDecisionEngine` asks Claude to choose among the permissible actions
  using its tool-use API, so the choice is structured and auditable instead of
  free text. It is strictly subordinate to the rules: any answer outside the
  permissible set, any malformed answer, and any API failure all fall back to the
  rule engine, so the agent keeps working with no API key and can never be talked
  into an action the clinic did not sanction.

Selection precedence for :attr:`ActionDecision.source`, which is recorded on every
decision for audit:

``rules``
    The rule engine answered (its own default, or the LLM is disabled/absent).
``llm``
    Claude chose, and the choice was permissible.
``llm-guardrail``
    Claude chose something impermissible or unparseable; rules overrode it.
``llm-error``
    The API call failed; rules answered instead.
"""

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional, Sequence

from core.actions import AgentAction
from core.config import ClinicPolicyConfig
from core.models import CaseStatus, ContactChannel, FollowUpCase

#: Statuses that still need attention. ``MESSAGE_SENT`` is included deliberately:
#: sending a reminder starts the wait, it does not end the case. Excluding it
#: froze ``reminder_count`` at 1 and made ``max_reminders_before_escalation``
#: unreachable, so a silent patient was never escalated to staff.
OPEN_STATUSES = (
    CaseStatus.PENDING,
    CaseStatus.MESSAGE_SENT,
    CaseStatus.AWAITING_REPLY,
)

#: Statuses where the case is finished and must not be contacted again.
CLOSED_STATUSES = (
    CaseStatus.BOOKED,
    CaseStatus.DECLINED,
    CaseStatus.ESCALATED,
)

#: Human-readable purpose of each action, shown to the model.
ACTION_DESCRIPTIONS: Dict[AgentAction, str] = {
    AgentAction.SEND_REMINDER: "Send another reminder; the patient has not replied yet",
    AgentAction.PROPOSE_SLOT: "Offer concrete appointment times to choose from",
    AgentAction.CONFIRM_BOOKING: "Book the appointment the patient agreed to",
    AgentAction.RESCHEDULE: "Move an already-booked appointment to a new time",
    AgentAction.ESCALATE_TO_STAFF: "Hand the case to a human; the agent cannot resolve it",
    AgentAction.MARK_DECLINED: "Record that the patient declined follow-up",
    AgentAction.DO_NOTHING: "Take no action now",
}


@dataclass
class DecisionContext:
    """
    Everything a decision engine is allowed to consider.

    Passed in rather than read off globals so a decision is reproducible and can
    be unit-tested by constructing one directly.

    Attributes:
        case: The case being decided.
        today: Date the cycle is running for.
        policy: Clinic policy in force.
        escalation_needed: Whether :class:`~agent.conversation.ConversationManager`
            already flags this case for human attention.
        available_channels: Channels this patient can actually be reached on.
        exhausted_channels: Channels already tried and known to have failed.
        impermissible: Actions the guardrail must reject, with the reason.
    """

    case: FollowUpCase
    today: date
    policy: ClinicPolicyConfig
    escalation_needed: bool = False
    available_channels: Sequence[ContactChannel] = ()
    exhausted_channels: Sequence[ContactChannel] = ()
    impermissible: Dict[AgentAction, str] = field(default_factory=dict)

    @property
    def days_since_contact(self) -> Optional[int]:
        """Days since the last contact attempt, or ``None`` if never contacted."""
        if self.case.last_contacted is None:
            return None
        return (self.today - self.case.last_contacted).days


@dataclass
class ActionDecision:
    """
    A chosen action together with the reason and who chose it.

    Attributes:
        action: The action to execute.
        rationale: Plain-language justification, written to the audit log.
        source: Which engine produced it (see the module docstring).
        alternatives: Actions that were permissible but not taken.
    """

    action: AgentAction
    rationale: str
    source: str = "rules"
    alternatives: List[AgentAction] = field(default_factory=list)

    @property
    def from_llm(self) -> bool:
        """Whether the model's own choice survived the guardrail."""
        return self.source == "llm"


class DecisionEngine(ABC):
    """Chooses one action for one case."""

    @abstractmethod
    def decide(self, context: DecisionContext) -> ActionDecision:
        """
        Choose an action.

        Args:
            context: Case facts and the permissible action space.

        Returns:
            The chosen action with its rationale. Never raises.
        """
        raise NotImplementedError

    @property
    def name(self) -> str:
        """Identifier used in audit entries."""
        return type(self).__name__


class RuleDecisionEngine(DecisionEngine):
    """
    The clinic's deterministic policy, expressed as a priority-ordered rule list.

    Doubles as the guardrail for any other engine: :meth:`permissible_actions`
    defines what may be chosen, so an LLM can be useful without being trusted.
    """

    def __init__(self, policy: ClinicPolicyConfig) -> None:
        """
        Args:
            policy: Clinic policy supplying the reminder interval and cap.
        """
        self.policy = policy

    def permissible_actions(self, context: DecisionContext) -> List[AgentAction]:
        """
        Actions allowed for this case, most-preferred first.

        The ordering is meaningful: :meth:`decide` takes the first entry, so this
        single method defines both the guardrail and the default policy.

        Args:
            context: Case facts.

        Returns:
            A non-empty list of actions.
        """
        case = context.case

        if case.status in CLOSED_STATUSES:
            return [AgentAction.DO_NOTHING]

        # Everything the agent can do has been tried and failed. Only a human can
        # progress this case now, so escalation is the only permissible action.
        if context.exhausted_channels and not self._untried_channels(context):
            return [AgentAction.ESCALATE_TO_STAFF]

        if self._escalation_due(context):
            return [AgentAction.ESCALATE_TO_STAFF]

        # The quiet period is a hard floor, not a preference, so it is not offered
        # to a decision engine as a choice.
        if self._inside_quiet_period(context):
            return [AgentAction.DO_NOTHING]

        if case.status in OPEN_STATUSES:
            # ``SEND_REMINDER`` first, so it stays the rule engine's default.
            actions = [AgentAction.SEND_REMINDER]

            # Offering concrete times is only useful once the patient has engaged.
            if case.status is CaseStatus.AWAITING_REPLY and self._untried_channels(context):
                actions.append(AgentAction.PROPOSE_SLOT)

            # On the last reminder the budget allows, asking a human is a
            # reasonable preference rather than an escalation the rules impose.
            if self._escalation_offered(context):
                actions.append(AgentAction.ESCALATE_TO_STAFF)

            # Waiting is always legitimate: a mildly overdue patient who ignored
            # one reminder may not need nagging today. It cannot lose the case,
            # because the budget check above still forces escalation eventually.
            actions.append(AgentAction.DO_NOTHING)
            return actions

        return [AgentAction.DO_NOTHING]

    def decide(self, context: DecisionContext) -> ActionDecision:
        """Return the most preferred permissible action."""
        permissible = self.permissible_actions(context)
        action = permissible[0]
        return ActionDecision(
            action=action,
            rationale=self._rationale(context, action),
            source="rules",
            alternatives=permissible[1:],
        )

    def _escalation_due(self, context: DecisionContext) -> bool:
        """
        Whether escalation is now mandatory.

        True when a human is already indicated, or when the clinic's reminder
        budget is spent. In either case only escalation is permissible.
        """
        if context.escalation_needed:
            return True
        return context.case.reminder_count >= self._reminder_cap(context)

    def _escalation_offered(self, context: DecisionContext) -> bool:
        """
        Whether asking for human help is a *permissible* choice rather than a rule.

        True on the final reminder the budget allows, so a decision engine can
        prefer to stop reminding and involve a person a cycle early.
        """
        return context.case.reminder_count >= self._reminder_cap(context) - 1

    @staticmethod
    def _reminder_cap(context: DecisionContext) -> int:
        """Reminders permitted before escalation, never less than one."""
        return max(1, context.policy.max_reminders_before_escalation)

    def _inside_quiet_period(self, context: DecisionContext) -> bool:
        """Whether the patient was contacted too recently to contact again."""
        elapsed = context.days_since_contact
        if elapsed is None:
            return False
        return elapsed < context.policy.reminder_interval_days

    @staticmethod
    def _untried_channels(context: DecisionContext) -> List[ContactChannel]:
        """Channels not yet known to have failed."""
        exhausted = set(context.exhausted_channels)
        return [c for c in context.available_channels if c not in exhausted]

    def _rationale(self, context: DecisionContext, action: AgentAction) -> str:
        """Explain a rule decision in the patient's own terms."""
        case = context.case
        facts = (
            f"{case.patient.name}: {case.days_overdue} days overdue, "
            f"urgency {case.urgency.value}, status {case.status.value}, "
            f"{case.reminder_count}/{self._reminder_cap(context)} reminders"
        )
        if context.exhausted_channels and not self._untried_channels(context):
            tried = ", ".join(c.value for c in context.exhausted_channels)
            return f"{facts} - every channel failed ({tried}); needs a human"
        if action is AgentAction.ESCALATE_TO_STAFF:
            if context.escalation_needed:
                return f"{facts} - human attention required"
            return f"{facts} - reminder budget exhausted"
        if action is AgentAction.SEND_REMINDER:
            elapsed = context.days_since_contact
            waited = "never contacted" if elapsed is None else f"{elapsed}d since last contact"
            return f"{facts} - {waited}, due for a reminder"
        if action is AgentAction.DO_NOTHING:
            elapsed = context.days_since_contact
            if elapsed is not None and self._inside_quiet_period(context):
                return (
                    f"{facts} - contacted {elapsed}d ago, waiting out "
                    f"{context.policy.reminder_interval_days}d interval"
                )
            return f"{facts} - case closed, nothing to do"
        return facts


#: Tool schema the model must answer with, so its decision is structured data
#: rather than prose that would have to be parsed.
DECISION_TOOL: Dict[str, Any] = {
    "name": "choose_next_action",
    "description": (
        "Choose the single next action for this patient follow-up case. "
        "You must pick one of the actions listed in the prompt."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [a.value for a in AgentAction],
                "description": "The action to take next.",
            },
            "rationale": {
                "type": "string",
                "description": (
                    "One or two sentences explaining why, referring to the case "
                    "facts. This is written to the audit log."
                ),
            },
        },
        "required": ["action", "rationale"],
    },
}

DECISION_SYSTEM_PROMPT = (
    "You are the decision component of a dental clinic's patient follow-up agent. "
    "For each case you pick exactly one next action, and you must pick it from the "
    "permitted actions given to you. Prefer the least intrusive action that can "
    "still move the case forward: repeated reminders that the patient ignores are "
    "worse than asking staff for help. When the patient has already been contacted "
    "as often as policy allows, escalate. Never diagnose; you are scheduling, not "
    "practising medicine. Answer only through the choose_next_action tool."
)

DEFAULT_LLM_MODEL = "claude-sonnet-4-5"
DEFAULT_MAX_TOKENS = 512


class LlmDecisionEngine(DecisionEngine):
    """
    Ask Claude to pick among the actions the rules permit.

    The model is given only the permissible set, and its answer is validated
    against that set before use. Consequently the worst an unreliable model can do
    is produce a decision identical to the rule engine's, which is also the
    fallback for a missing client, a malformed response, or any API error.

    ``client`` is any object shaped like ``anthropic.Anthropic`` (exposing
    ``messages.create``), so tests drive it with
    :class:`tools.llm_agent.ScriptedModelClient` and no API key.
    """

    def __init__(
        self,
        policy: ClinicPolicyConfig,
        client: Optional[Any] = None,
        model: str = DEFAULT_LLM_MODEL,
        inner: Optional[RuleDecisionEngine] = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> None:
        """
        Args:
            policy: Clinic policy, also used to build the default guardrail.
            client: Anthropic-compatible client. When ``None``, the engine
                behaves exactly like the rule engine.
            model: Model id to request.
            inner: Guardrail engine. Defaults to a rule engine on ``policy``.
            max_tokens: Response budget for the decision call.
        """
        self.policy = policy
        self.inner = inner or RuleDecisionEngine(policy)
        self.model = model
        self.max_tokens = max_tokens
        self._client = client
        self.last_error: Optional[str] = None

    @classmethod
    def from_environment(
        cls,
        policy: ClinicPolicyConfig,
        *,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
    ) -> "LlmDecisionEngine":
        """
        Build an engine backed by whichever model the clinic configured.

        The vendor is chosen by ``AGENT_LLM_PROVIDER`` (see
        :mod:`tools.llm_providers`); the Anthropic path is the default. Returns an
        engine with no client -- i.e. pure rule behaviour -- when no usable model
        is configured, so an offline demo, CI run or test suite is unaffected.

        Args:
            policy: Clinic policy.
            api_key: Explicit credential, overriding the environment.
            model: Model id to request. Defaults to ``AGENT_LLM_MODEL``, then
                ``AGENT_DECISION_MODEL``, then the vendor default.

        Returns:
            An engine ready to use, possibly in rules-only mode.
        """
        from tools.llm_providers import LlmProviderConfig, create_llm_client

        config = LlmProviderConfig.from_env()
        if model:
            config.model = model
        elif not config.model:
            config.model = os.environ.get("AGENT_DECISION_MODEL") or DEFAULT_LLM_MODEL
        if api_key:
            config.api_key = api_key

        try:
            client = create_llm_client(config)
        except Exception:
            # Missing package, missing key, bad key, malformed endpoint: rules-only
            # is the right answer because an offline run must never depend on the
            # model being reachable. The clinic can switch vendor with
            # AGENT_LLM_PROVIDER without touching this code.
            return cls(policy, client=None, model=config.model)
        return cls(policy, client=client, model=config.model)

    @property
    def client(self) -> Optional[Any]:
        """The underlying client, if this engine has one."""
        return self._client

    def decide(self, context: DecisionContext) -> ActionDecision:
        """
        Ask the model, then validate its answer against the rule guardrail.
        """
        permissible = self.inner.permissible_actions(context)

        if self._client is None:
            return self._fallback(context, "llm-disabled", None)
        if len(permissible) == 1:
            # Nothing to choose: asking would only risk overriding the rules.
            return self._fallback(context, "rules", None)

        try:
            response = self._client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=DECISION_SYSTEM_PROMPT,
                tools=[DECISION_TOOL],
                tool_choice={"type": "tool", "name": DECISION_TOOL["name"]},
                messages=[{"role": "user", "content": self._prompt(context, permissible)}],
            )
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return self._fallback(context, "llm-error", self.last_error)

        return self._interpret(response, context, permissible)

    def _interpret(
        self,
        response: Any,
        context: DecisionContext,
        permissible: List[AgentAction],
    ) -> ActionDecision:
        """Validate the model's tool call, falling back when it is unusable."""
        payload = _extract_tool_input(response)
        if payload is None:
            self.last_error = "model did not call choose_next_action"
            return self._fallback(context, "llm-guardrail", self.last_error)

        raw_action = payload.get("action")
        try:
            chosen = AgentAction(raw_action)
        except ValueError:
            self.last_error = f"unknown action {raw_action!r}"
            return self._fallback(context, "llm-guardrail", self.last_error)

        if chosen not in permissible:
            permitted = ", ".join(a.value for a in permissible)
            self.last_error = (
                f"action {chosen.value!r} is not permitted here (allowed: {permitted})"
            )
            return self._fallback(context, "llm-guardrail", self.last_error)

        rationale = str(payload.get("rationale") or "").strip() or (
            ACTION_DESCRIPTIONS.get(chosen, chosen.value)
        )
        self.last_error = None
        return ActionDecision(
            action=chosen,
            rationale=f"[llm] {rationale}",
            source="llm",
            alternatives=[a for a in permissible if a is not chosen],
        )

    def _fallback(
        self, context: DecisionContext, source: str, reason: Optional[str]
    ) -> ActionDecision:
        """Use the guardrail engine's answer, noting why."""
        decision = self.inner.decide(context)
        decision.source = source
        if reason:
            decision.rationale = f"{decision.rationale} ({source}: {reason})"
        return decision

    @staticmethod
    def _prompt(
        context: DecisionContext, permissible: List[AgentAction]
    ) -> str:
        """Render the case as the model needs to see it."""
        case = context.case
        elapsed = context.days_since_contact
        channels = ", ".join(c.value for c in context.available_channels) or "none on file"
        exhausted = (
            ", ".join(c.value for c in context.exhausted_channels) or "none"
        )
        options = "\n".join(
            f"- {action.value}: {ACTION_DESCRIPTIONS.get(action, '')}"
            for action in permissible
        )
        return (
            f"Patient: {case.patient.name}\n"
            f"Language: {case.patient.language}\n"
            f"Treatment: {case.patient.treatment_type}\n"
            f"Days overdue: {case.days_overdue}\n"
            f"Urgency: {case.urgency.value}\n"
            f"Case status: {case.status.value}\n"
            f"Reason for follow-up: {case.reason}\n"
            f"Reminders sent: {case.reminder_count} of "
            f"{context.policy.max_reminders_before_escalation}\n"
            f"Days since last contact: {'never' if elapsed is None else elapsed}\n"
            f"Minimum days between reminders: {context.policy.reminder_interval_days}\n"
            f"Reachable channels: {channels}\n"
            f"Channels already tried and failed: {exhausted}\n"
            f"\nPermitted actions (choose exactly one):\n{options}\n"
        )


def _extract_tool_input(response: Any) -> Optional[Dict[str, Any]]:
    """
    Pull the ``choose_next_action`` arguments out of an Anthropic-shaped response.

    Tolerant of plain mappings as well as SDK objects, so the same code path is
    exercised by :class:`tools.llm_agent.ScriptedModelClient` in tests.

    Args:
        response: Raw model response.

    Returns:
        The tool input mapping, or ``None`` when the model produced no usable call.
    """
    for block in _get(response, "content") or []:
        if _get(block, "type") != "tool_use":
            continue
        if _get(block, "name") not in (None, DECISION_TOOL["name"]):
            continue
        tool_input = _get(block, "input")
        if isinstance(tool_input, dict) and "action" in tool_input:
            return tool_input
    return None


def _get(obj: Any, key: str, default: Any = None) -> Any:
    """Read ``key`` from a mapping or an attribute, whichever the object is."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)
