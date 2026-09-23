"""
Main agent orchestrator - the "brain" of the Patient Follow-up Agent.

This module implements the core agentic loop:
Perceive -> Decide -> Act -> Observe

The orchestrator coordinates all subsystems to autonomously manage
the entire patient follow-up workflow, from identification through
resolution or escalation.
"""

import os
from datetime import date, timedelta
from typing import Dict, List, Optional

from core.models import FollowUpCase, CaseStatus, ContactChannel
from core.config import ClinicPolicyConfig
from core.data_access import PatientDataStore, CalendarIntegration
from core.actions import AgentAction
from agent.business_rules import RecallRuleEngine, UrgencyScorer
from agent.decision import (
    ActionDecision, DecisionContext, DecisionEngine, LlmDecisionEngine,
    RuleDecisionEngine,
)
from agent.delivery import (
    DeliveryBackend, NotificationOutcome, missing_recipient_outcome,
)
from agent.notifications import (
    NotificationChannel, MessageComposerAgent, build_notification_channels,
)
from agent.conversation import ConversationManager
from agent.action_handlers import AppointmentScheduler, EscalationHandler, AuditLogger

#: Statuses that still need the agent's attention. ``MESSAGE_SENT`` is included on
#: purpose: a reminder that went out does not end the case, it starts the wait. If
#: it did terminate the case, ``reminder_count`` could never reach
#: ``max_reminders_before_escalation`` and a non-responding patient would be
#: silently abandoned.
_OPEN_STATUSES = (
    CaseStatus.PENDING,
    CaseStatus.MESSAGE_SENT,
    CaseStatus.AWAITING_REPLY,
)

#: Statuses that are finished, whatever the reason, and must never be re-contacted.
CLOSED_STATUSES = (
    CaseStatus.BOOKED,
    CaseStatus.DECLINED,
    CaseStatus.ESCALATED,
)


class FollowUpAgentOrchestrator:
    """
    The central controller that orchestrates the entire agent workflow.

    This class embodies the "agentic loop" pattern:
    1. PERCEIVE: Gather data about overdue patients from the data store
    2. DECIDE: Evaluate urgency, prioritize cases, determine actions
    3. ACT: Execute actions (send messages, book appointments, escalate)
    4. OBSERVE: Process patient responses and update case states

    The orchestrator demonstrates autonomous decision-making while
    maintaining transparency, auditability, and appropriate escalation
    to human staff when needed.
    """

    def __init__(
        self,
        data_store: PatientDataStore,
        calendar: CalendarIntegration,
        policy: Optional[ClinicPolicyConfig] = None,
        notification_channels: Optional[Dict[ContactChannel, NotificationChannel]] = None,
        delivery_backend: Optional[DeliveryBackend] = None,
        decision_engine: Optional["DecisionEngine"] = None,
        escalation_email: Optional[str] = None,
    ):
        """
        Initialize the agent orchestrator with all required subsystems.

        Args:
            data_store: Patient data access layer
            calendar: Calendar/scheduling system integration
            policy: Clinic policy configuration (uses defaults if not provided)
            notification_channels: Pre-built channels. When omitted, they are built
                from the environment (live when ``MESSAGING_DRY_RUN=0``, offline
                otherwise), so the demo and tests keep working with no credentials.
            delivery_backend: Backend to build the default channels with.
            decision_engine: Action-selection strategy. Defaults to the rule engine,
                which is also what any LLM-backed engine falls back to.
            escalation_email: Staff address to alert when a case is escalated.
                When omitted, escalations are recorded but nobody is emailed.
        """
        # Store dependencies
        self.data_store = data_store
        self.calendar = calendar
        self.policy = policy or ClinicPolicyConfig.from_env()

        # Initialize core business logic components
        self.rule_engine = RecallRuleEngine(self.policy)
        self.urgency_scorer = UrgencyScorer(self.policy)

        # Initialize communication components
        self.message_composer = MessageComposerAgent()
        self.conversation_manager = ConversationManager()

        # Initialize notification channels
        self.delivery_backend = delivery_backend
        self.notification_channels = notification_channels or build_notification_channels(
            delivery_backend
        )

        # Initialize action handlers
        self.scheduler = AppointmentScheduler(calendar)
        self.escalation_email = escalation_email or os.environ.get("AGENT_ESCALATION_EMAIL") or None
        self.escalation_handler = EscalationHandler(
            alert_email=self.escalation_email,
            notifier=self._alert_staff if self.escalation_email else None,
        )
        self.audit_logger = AuditLogger()

        # Decision strategy, and a record of what the agent could not deliver
        self.decision_engine = decision_engine or RuleDecisionEngine(self.policy)
        self.undelivered: List[Dict[str, object]] = []

        # Active cases being managed by the agent
        self.active_cases: dict[str, FollowUpCase] = {}

    @classmethod
    def with_llm_decisions(
        cls,
        data_store: PatientDataStore,
        calendar: CalendarIntegration,
        policy: Optional[ClinicPolicyConfig] = None,
        *,
        model: Optional[str] = None,
        **kwargs: object,
    ) -> "FollowUpAgentOrchestrator":
        """
        Build an orchestrator whose DECIDE step is driven by Claude.

        The LLM only ever selects from the actions the rule engine permits, so
        this cannot send a message the rules would not have allowed. With no API
        key (or no ``anthropic`` install) it silently degrades to the rule
        engine, which is why this is safe to use as the default in the web app.

        The leading parameters mirror :meth:`__init__` exactly rather than being
        forwarded through ``*args``. That matters: a caller passing ``policy``
        positionally must not collide with a ``policy`` keyword added here, which
        is precisely the failure this signature exists to prevent.

        Args:
            data_store: Patient data access layer.
            calendar: Calendar/scheduling integration.
            policy: Clinic policy, or ``None`` for the defaults.
            model: Model id override; otherwise ``AGENT_DECISION_MODEL``.
            **kwargs: Remaining ``__init__`` arguments.

        Returns:
            A configured orchestrator.
        """
        resolved_policy = policy or ClinicPolicyConfig.from_env()
        kwargs.setdefault(
            "decision_engine",
            LlmDecisionEngine.from_environment(resolved_policy, model=model),
        )
        return cls(data_store, calendar, resolved_policy, **kwargs)  # type: ignore[arg-type]

    def run_daily_cycle(self, today: Optional[date] = None) -> list[FollowUpCase]:
        """
        Execute one complete daily cycle of the agent workflow.

        This is the main entry point for the agent's autonomous operation.
        Typically called once per day (or on-demand) to process all
        overdue follow-ups.

        The cycle follows the agentic loop:
        1. PERCEIVE: Identify overdue patients
        2. DECIDE: Score urgency and prioritize
        3. ACT: Send reminders and take appropriate actions
        4. OBSERVE: (handled separately when replies come in)

        Args:
            today: Reference date (defaults to today)

        Returns:
            List of all cases processed in this cycle
        """
        if today is None:
            today = date.today()

        print(f"\n{'='*70}")
        print(f"🤖 AGENT DAILY CYCLE - {today.isoformat()}")
        print(f"{'='*70}\n")

        # === PHASE 1: PERCEIVE ===
        print("📊 PHASE 1: PERCEIVE - Gathering patient data...")
        all_patients = self.data_store.get_all_active_patients()
        print(f"   Found {len(all_patients)} active patients")

        # Identify overdue patients using rule engine
        overdue_cases = self.rule_engine.compute_overdue_patients(all_patients, today)
        print(f"   Identified {len(overdue_cases)} overdue patients\n")

        # === PHASE 2: DECIDE ===
        print("🧠 PHASE 2: DECIDE - Evaluating urgency and prioritizing...")

        # Score urgency for each case
        for case in overdue_cases:
            case.urgency = self.urgency_scorer.score(case)
            print(f"   {case.patient.name}: {case.urgency.value.upper()} "
                  f"({case.days_overdue} days overdue)")

        # Sort by urgency (most urgent first)
        prioritized_cases = self.urgency_scorer.sort_by_urgency(overdue_cases)
        print(f"\n   Prioritized {len(prioritized_cases)} cases by urgency\n")

        # === PHASE 3: ACT ===
        print("⚡ PHASE 3: ACT - Taking actions on prioritized cases...")

        processed_cases = []
        for case in prioritized_cases:
            # A fresh case object is built from the data store on every cycle, so
            # it knows the new ``days_overdue`` but nothing about what the agent
            # has already tried. Carry that progress forward, otherwise the
            # reminder counter resets to zero every day and the escalation cap
            # can never be reached.
            previous = self.active_cases.get(case.patient.patient_id)
            if previous is not None:
                self._carry_forward(previous, case)
            self.active_cases[case.patient.patient_id] = case

            # Decide and execute action for this case
            decision = self._decide_for_case(case, today)
            self._execute_action(case, decision, today)

            processed_cases.append(case)

        undelivered = len(self.undelivered)
        print(f"\n✅ Daily cycle complete. Processed {len(processed_cases)} cases.")
        if undelivered:
            affected = len({record["patient_id"] for record in self.undelivered})
            print(f"⚠️  {undelivered} send attempt(s) failed across {affected} case(s); "
                  f"affected cases were escalated to staff. Run with a live backend "
                  f"or check the audit log for details.")
        print()
        print(f"{'='*70}\n")

        return processed_cases

    def _available_channels_for(self, case: FollowUpCase) -> List[ContactChannel]:
        """
        Channels this patient can actually be reached on.

        The preferred channel comes first so it stays the primary choice, followed
        by every other channel the patient has a contact detail for. ``PHONE_CALL``
        is included even though the agent cannot place calls: it is a real
        preference worth reflecting, and its failure is informative.

        Args:
            case: Case whose patient is being contacted.

        Returns:
            Ordered channel list, possibly empty.
        """
        patient = case.patient
        ordered: List[ContactChannel] = []

        if patient.contact_info.get(patient.preferred_channel):
            ordered.append(patient.preferred_channel)

        for channel in ContactChannel:
            if channel in ordered:
                continue
            if patient.contact_info.get(channel):
                ordered.append(channel)

        return ordered

    def _exhausted_channels_for(self, case: FollowUpCase) -> List[ContactChannel]:
        """Channels already tried for this case and still failing."""
        patient_id = case.patient.patient_id
        seen: List[ContactChannel] = []
        for record in self.undelivered:
            if record.get("patient_id") != patient_id:
                continue
            channel = record.get("channel")
            if isinstance(channel, ContactChannel) and channel not in seen:
                seen.append(channel)
        return seen

    @staticmethod
    def _carry_forward(previous: FollowUpCase, current: FollowUpCase) -> None:
        """
        Transfer a case's progress from a previous cycle onto a fresh perception.

        The freshly-perceived case has newer facts (days overdue, urgency); the
        previously-tracked one has the agent's history (how many reminders were
        sent, what the patient said). The agent needs both, so the history is
        copied over the fresh object rather than being thrown away.

        Args:
            previous: The case as tracked at the end of an earlier cycle.
            current: The case just rebuilt from the data store; mutated in place.
        """
        current.status = previous.status
        current.reminder_count = previous.reminder_count
        if previous.last_contacted is not None:
            current.last_contacted = previous.last_contacted
        if previous.conversation_log:
            current.conversation_log = list(previous.conversation_log)

    def _alert_staff(self, escalation: dict) -> None:
        """
        Email a human about an escalated case.

        Routed through the same backend as patient messages, so it obeys the same
        dry-run/live gate: a real email only when live sends are explicitly on,
        otherwise it is printed. Failures propagate to the caller, which records
        them on the case rather than aborting the cycle.

        Args:
            escalation: The record produced by
                :meth:`~agent.action_handlers.EscalationHandler.escalate`.
        """
        channel = self.notification_channels.get(ContactChannel.EMAIL)
        if channel is None:
            raise RuntimeError("no email channel is configured to alert staff")
        if not self.escalation_email:
            # Unreachable via the constructor, which only wires this method as the
            # notifier when an address exists. Raised anyway so the invariant is
            # local and a future caller cannot silently drop an escalation.
            raise RuntimeError("no escalation email is configured to alert staff")

        subject = f"[{escalation['priority'].upper()}] Escalation: {escalation['patient_name']}"
        body = "\n".join(
            [
                "A patient follow-up case needs human attention.",
                "",
                f"Patient:     {escalation['patient_name']} ({escalation['patient_id']})",
                f"Treatment:   {escalation['treatment_type']}",
                f"Days overdue:{escalation['days_overdue']}",
                f"Urgency:     {escalation['urgency']}",
                f"Priority:    {escalation['priority']}",
                f"Reason:      {escalation['reason']}",
                f"Escalated:   {escalation['escalated_at']}",
                "",
                "Conversation log:",
                *(f"  - {entry}" for entry in escalation["conversation_log"]),
                "",
                "Please contact this patient directly.",
            ]
        )

        outcome = channel.backend.deliver(
            ContactChannel.EMAIL,
            self.escalation_email,
            body,
            subject=subject,
        )
        if not outcome.success:
            raise RuntimeError(
                f"{outcome.error_code}: {outcome.error_message} (via {outcome.channel.value})"
            )

    def _decide_for_case(self, case: FollowUpCase, today: date) -> ActionDecision:
        """
        Decide what action to take for a specific case.

        Builds the decision context from the case, the clinic policy and what the
        agent already knows about this patient's reachability, then delegates to
        the configured decision engine. The rule engine's permissible-action set is
        what the engine is bound by, so a model-backed engine cannot act outside
        clinic policy.

        Args:
            case: Follow-up case to process
            today: Current date

        Returns:
            The chosen action with its rationale and provenance.
        """
        context = DecisionContext(
            case=case,
            today=today,
            policy=self.policy,
            escalation_needed=self.conversation_manager.should_escalate(case),
            available_channels=self._available_channels_for(case),
            exhausted_channels=self._exhausted_channels_for(case),
        )
        return self.decision_engine.decide(context)

    def _decide_action_for_case(self, case: FollowUpCase, today: date) -> AgentAction:
        """
        Decide the next action for a case, returning just the action.

        Retained for callers that only want the action; the rationale and the
        provenance of the decision are available via :meth:`_decide_for_case`.

        Args:
            case: Follow-up case to process
            today: Current date

        Returns:
            AgentAction to execute.
        """
        return self._decide_for_case(case, today).action

    def _deliver_with_fallback(
        self,
        case: FollowUpCase,
        message: str,
        context: str = "reminder",
        today: Optional[date] = None,
    ) -> NotificationOutcome:
        """
        Try to deliver a message, moving on to another channel when one fails.

        This is the agent's answer to "the recipient was not verified". A failed
        send is not an exception and not a reason to give up: it is information.
        The channel's own ``suggested_fallbacks`` are honoured first (they come from
        the provider's error taxonomy), then any other channel the patient is
        reachable on, so a patient without WhatsApp still gets the SMS.

        Every attempt is written to the audit log and the conversation log, so the
        trail shows what was tried and why each attempt failed.

        Args:
            case: Case being contacted.
            message: Body to send.
            context: What is being sent, used in log entries.
            today: Date for log entries.

        Returns:
            The successful outcome, or the last failure when nothing got through.
        """
        patient = case.patient
        order = self._deliverable_order(case)
        if not order:
            return missing_recipient_outcome(patient.preferred_channel)

        last_failure: Optional[NotificationOutcome] = None

        # Every channel the patient is reachable on gets exactly one attempt. A
        # provider's ``suggested_fallbacks`` is advisory (it describes the error,
        # not this patient), so it must not be used to give up early: an SMS
        # rejection says nothing about whether the email address works.
        for channel_type in order:
            channel = self.notification_channels.get(channel_type)
            if channel is None:
                continue

            outcome = channel.send(patient, message)
            self.audit_logger.log_communication(
                case, outcome.channel.value, message, "outbound", outcome.success
            )

            if outcome.success:
                case.add_to_log(
                    f"{context} delivered via {outcome.channel.value}"
                    f"{' (simulated)' if outcome.simulated else ''}"
                )
                return outcome

            case.add_to_log(
                f"{context} failed via {outcome.channel.value} "
                f"[{outcome.error_code}]: {outcome.error_message}"
            )
            self._record_undelivered(case, outcome, context)
            last_failure = outcome

        return last_failure or missing_recipient_outcome(patient.preferred_channel)

    def _deliverable_order(self, case: FollowUpCase) -> List[ContactChannel]:
        """
        Channels to attempt, in order, for this case.

        Preferred channel first, then any other channel the patient is reachable
        on that has not already failed for this case.
        """
        exhausted = set(self._exhausted_channels_for(case))
        order = [
            c for c in self._available_channels_for(case) if c not in exhausted
        ]
        return order

    def _record_undelivered(
        self, case: FollowUpCase, outcome: NotificationOutcome, context: str
    ) -> None:
        """Remember a failed attempt so later cycles know what has been tried."""
        self.undelivered.append(
            {
                "patient_id": case.patient.patient_id,
                "channel": outcome.channel,
                "error_code": outcome.error_code,
                "context": context,
                "message_id": outcome.message_id,
            }
        )

    def _escalate_undeliverable(
        self,
        case: FollowUpCase,
        outcome: NotificationOutcome,
        today: date,
    ) -> None:
        """
        Escalate a case the agent could not reach, rather than reporting success.

        Args:
            case: Case that could not be contacted.
            outcome: The final, unsuccessful delivery outcome.
            today: Date of the attempt.
        """
        tried = ", ".join(c.value for c in self._exhausted_channels_for(case)) or "none"
        reason = (
            f"Undeliverable: no channel reached {case.patient.name}. "
            f"Channels tried: {tried}. Last error: "
            f"{outcome.error_code} ({outcome.error_message}). "
            f"{outcome.hint or ''}"
        ).strip()

        self.escalation_handler.escalate(case, reason, "high")
        case.status = CaseStatus.ESCALATED
        case.add_to_log(f"Escalated: undeliverable ({tried})")
        self.audit_logger.log_decision(
            case,
            AgentAction.ESCALATE_TO_STAFF,
            f"Escalated after delivery failure on {outcome.channel.value}: "
            f"{outcome.error_code}",
        )

        print(f"   ⚠️  Could not reach {case.patient.name} on any channel "
              f"({tried}); escalated to staff")
        if outcome.hint:
            print(f"       Reason: {outcome.hint}")

    def _execute_action(
        self, case: FollowUpCase, decision: ActionDecision, today: date
    ) -> None:
        """
        Execute a decided action for a case.

        This is where the agent's decisions become concrete actions
        in the real world (sending messages, booking appointments, etc.).

        Args:
            case: Follow-up case
            decision: The decision to act on
            today: Current date
        """
        patient = case.patient
        action = decision.action

        # Log the decision, including which engine produced it
        rationale = (
            f"Action: {action.value} for {patient.name} "
            f"(Urgency: {case.urgency.value}, Status: {case.status.value}, "
            f"Decided by: {decision.source})"
        )
        # Record which engine chose, so a reviewer can always tell a model's
        # judgement from the rules' default.
        self.audit_logger.log_decision(
            case,
            action,
            rationale,
            additional_context={
                "decision_source": decision.source,
                "alternatives_considered": [a.value for a in decision.alternatives],
            },
        )
        if decision.from_llm:
            print(f"   🧠 {patient.name}: model chose {action.value} "
                  f"({decision.rationale})")
        if action == AgentAction.SEND_REMINDER:
            # Compose personalized message
            message_type = "urgent" if case.urgency.value == "critical" else "initial"
            message = self.message_composer.compose(case, message_type)

            outcome = self._deliver_with_fallback(
                case, message, context="reminder", today=today
            )

            if outcome.success:
                case.status = CaseStatus.MESSAGE_SENT
                case.last_contacted = today
                case.reminder_count += 1
                case.add_to_log(f"Sent reminder via {outcome.channel.value}")
                self.data_store.update_last_contacted(patient.patient_id, today)

                print(f"   ✉️  Sent reminder to {patient.name} via "
                      f"{outcome.channel.value}")
            else:
                # Nothing got through. Sending more reminders is pointless, so hand
                # the case to a human instead of pretending it was handled.
                self._escalate_undeliverable(case, outcome, today)
        elif action == AgentAction.PROPOSE_SLOT:
            # Find available appointment slots
            available_slots = self.scheduler.find_available_slots(
                case, after=today, limit=3
            )

            if available_slots:
                # Compose message with slot options
                message = self.message_composer.compose_slot_proposal(
                    case, available_slots
                )

                # Send message, falling back across channels like any other send
                outcome = self._deliver_with_fallback(
                    case, message, context="slot proposal", today=today
                )
                if outcome.success:
                    case.status = CaseStatus.AWAITING_REPLY
                    case.add_to_log(f"Proposed {len(available_slots)} appointment slots")
                    print(f"   📅 Proposed appointment slots to {patient.name} "
                          f"via {outcome.channel.value}")
                else:
                    self._escalate_undeliverable(case, outcome, today)

        elif action == AgentAction.CONFIRM_BOOKING:
            # Book the appointment
            success, booked_date = self.scheduler.try_book(case)

            # Log appointment action
            self.audit_logger.log_appointment_action(
                case, "booking", booked_date, success
            )

            if success:
                case.add_to_log(f"Appointment booked for {booked_date.isoformat()}")
                print(f"   ✅ Booked appointment for {patient.name} on {booked_date}")

                # Send confirmation message; a booking that the patient never hears
                # about is a no-show waiting to happen, so a total delivery failure
                # is escalated rather than swallowed.
                confirmation_msg = (
                    "Your appointment is confirmed for "
                    f"{booked_date.strftime('%A, %B %d')}. See you then!"
                )
                outcome = self._deliver_with_fallback(
                    case, confirmation_msg, context="booking confirmation", today=today
                )
                if not outcome.success:
                    self._escalate_undeliverable(case, outcome, today)

        elif action == AgentAction.ESCALATE_TO_STAFF:
            # Determine escalation priority based on urgency
            priority_map = {
                "critical": "critical",
                "high": "high",
                "medium": "normal",
                "low": "low"
            }
            priority = priority_map.get(case.urgency.value, "normal")

            # Escalate to human staff
            reason = f"Case requires human attention: {case.reminder_count} reminders sent, " \
                    f"{case.days_overdue} days overdue, urgency: {case.urgency.value}"

            self.escalation_handler.escalate(case, reason, priority)
            print(f"   ⚠️  Escalated {patient.name} to staff (Priority: {priority})")

        elif action == AgentAction.MARK_DECLINED:
            case.status = CaseStatus.DECLINED
            case.add_to_log("Patient declined follow-up")
            print(f"   ℹ️  Marked {patient.name} as declined")

        elif action == AgentAction.DO_NOTHING:
            # No action needed at this time
            pass

    def handle_incoming_reply(
        self, patient_id: str, message: str, received_date: Optional[date] = None
    ) -> None:
        """
        Process an incoming reply from a patient.

        This completes the agentic loop by OBSERVING patient responses
        and deciding next actions based on their reply.

        This method demonstrates the agent's ability to handle dynamic,
        multi-turn conversations and make contextual decisions.

        Args:
            patient_id: ID of patient who sent the message
            message: Patient's message text
            received_date: Date message was received (defaults to today)
        """
        if received_date is None:
            received_date = date.today()

        # Retrieve the case for this patient
        case = self.active_cases.get(patient_id)
        if not case:
            print(f"⚠️  Received message from unknown patient: {patient_id}")
            return

        print(f"\n📬 Incoming message from {case.patient.name}")
        print(f"   Message: \"{message}\"")

        # Update case status
        case.status = CaseStatus.AWAITING_REPLY

        # Use conversation manager to understand intent and decide action
        action, context = self.conversation_manager.handle_reply(case, message)

        # Log the communication
        self.audit_logger.log_communication(
            case, case.patient.preferred_channel.value, message,
            "inbound", True
        )

        print(f"   🧠 Recognized intent, decided action: {action.value}")

        # Generate response message
        response = self.conversation_manager.generate_response(action, case, context)

        # Execute the decided action
        if action == AgentAction.CONFIRM_BOOKING:
            # Extract preferred slot if provided
            preferred_slot = context.get('selected_slot')
            if preferred_slot:
                # Get available slots
                slots = self.scheduler.find_available_slots(
                    case, after=received_date, limit=5
                )
                if preferred_slot <= len(slots):
                    selected_date = slots[preferred_slot - 1]
                    success, booked_date = self.scheduler.try_book(case, selected_date)

                    if success:
                        response = f"Perfect! Your appointment is confirmed for " \
                                  f"{booked_date.strftime('%A, %B %d')}. See you then!"
                        self.audit_logger.log_appointment_action(
                            case, "booking", booked_date, True
                        )
            else:
                # Try to book next available
                success, booked_date = self.scheduler.try_book(case)
                if success and booked_date:
                    response = f"Great! We've booked you for " \
                              f"{booked_date.strftime('%A, %B %d')}."

        elif action == AgentAction.PROPOSE_SLOT:
            # Find new slots
            available_slots = self.scheduler.find_available_slots(
                case, after=received_date, limit=3
            )
            if available_slots:
                response = self.message_composer.compose_slot_proposal(
                    case, available_slots
                )

        elif action == AgentAction.ESCALATE_TO_STAFF:
            self.escalation_handler.escalate(
                case,
                reason=f"Patient question or complex request: {message}",
                priority="normal"
            )

        # Send response
        if response:
            outcome = self._deliver_with_fallback(
                case, response, context="reply", today=received_date
            )
            if outcome.success:
                case.add_to_log(f"Agent: {response}")
                print(f"   💬 Sent response via {outcome.channel.value}: "
                      f"\"{response[:60]}...\"")
            else:
                # The patient is waiting for an answer the agent cannot deliver.
                # Silence would look like being ignored, so escalate to a human.
                self._escalate_undeliverable(case, outcome, received_date)

        print()

    def get_active_cases(self) -> list[FollowUpCase]:
        """
        Get all currently active follow-up cases.

        Returns:
            List of active cases
        """
        return list(self.active_cases.values())

    def get_case_by_patient_id(self, patient_id: str) -> Optional[FollowUpCase]:
        """
        Retrieve a specific case by patient ID.

        Args:
            patient_id: Patient identifier

        Returns:
            FollowUpCase if found, None otherwise
        """
        return self.active_cases.get(patient_id)

    def get_statistics(self) -> dict:
        """
        Generate operational statistics for the agent.

        Returns:
            Dictionary containing various metrics
        """
        cases = list(self.active_cases.values())

        status_counts = {}
        for status in CaseStatus:
            status_counts[status.value] = sum(
                1 for case in cases if case.status == status
            )

        urgency_counts = {}
        for case in cases:
            urgency_counts[case.urgency.value] = \
                urgency_counts.get(case.urgency.value, 0) + 1

        audit_summary = self.audit_logger.generate_summary_report()

        return {
            "total_active_cases": len(cases),
            "cases_by_status": status_counts,
            "cases_by_urgency": urgency_counts,
            "escalated_cases": len(self.escalation_handler.get_escalated_cases()),
            "audit_summary": audit_summary,
        }
