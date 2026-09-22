"""
Main agent orchestrator - the "brain" of the Patient Follow-up Agent.

This module implements the core agentic loop:
Perceive -> Decide -> Act -> Observe

The orchestrator coordinates all subsystems to autonomously manage
the entire patient follow-up workflow, from identification through
resolution or escalation.
"""

from datetime import date, timedelta
from typing import Optional

from core.models import FollowUpCase, CaseStatus, ContactChannel
from core.config import ClinicPolicyConfig
from core.data_access import PatientDataStore, CalendarIntegration
from core.actions import AgentAction
from agent.business_rules import RecallRuleEngine, UrgencyScorer
from agent.notifications import (
    NotificationChannel, SMSChannel, WhatsAppChannel, 
    EmailChannel, PhoneCallChannel, MessageComposerAgent
)
from agent.conversation import ConversationManager
from agent.action_handlers import AppointmentScheduler, EscalationHandler, AuditLogger


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
    ):
        """
        Initialize the agent orchestrator with all required subsystems.
        
        Args:
            data_store: Patient data access layer
            calendar: Calendar/scheduling system integration
            policy: Clinic policy configuration (uses defaults if not provided)
        """
        # Store dependencies
        self.data_store = data_store
        self.calendar = calendar
        self.policy = policy or ClinicPolicyConfig()
        
        # Initialize core business logic components
        self.rule_engine = RecallRuleEngine(self.policy)
        self.urgency_scorer = UrgencyScorer(self.policy)
        
        # Initialize communication components
        self.message_composer = MessageComposerAgent()
        self.conversation_manager = ConversationManager()
        
        # Initialize notification channels
        self.notification_channels = {
            ContactChannel.SMS: SMSChannel(),
            ContactChannel.WHATSAPP: WhatsAppChannel(),
            ContactChannel.EMAIL: EmailChannel(),
            ContactChannel.PHONE_CALL: PhoneCallChannel(),
        }
        
        # Initialize action handlers
        self.scheduler = AppointmentScheduler(calendar)
        self.escalation_handler = EscalationHandler()
        self.audit_logger = AuditLogger()
        
        # Active cases being managed by the agent
        self.active_cases: dict[str, FollowUpCase] = {}

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
            # Add to active cases if not already tracked
            if case.patient.patient_id not in self.active_cases:
                self.active_cases[case.patient.patient_id] = case
            else:
                # Update existing case with new information
                self.active_cases[case.patient.patient_id] = case
            
            # Decide and execute action for this case
            action = self._decide_action_for_case(case, today)
            self._execute_action(case, action, today)
            
            processed_cases.append(case)
        
        print(f"\n✅ Daily cycle complete. Processed {len(processed_cases)} cases.\n")
        print(f"{'='*70}\n")
        
        return processed_cases

    def _decide_action_for_case(
        self, case: FollowUpCase, today: date
    ) -> AgentAction:
        """
        Decide what action to take for a specific case.
        
        This is a key decision point demonstrating autonomous reasoning.
        The agent considers multiple factors to choose the best action.
        
        Args:
            case: Follow-up case to process
            today: Current date
            
        Returns:
            AgentAction to execute
        """
        # Check if case should be escalated
        if self.conversation_manager.should_escalate(case):
            return AgentAction.ESCALATE_TO_STAFF
        
        # Check if we've exceeded reminder threshold
        if case.reminder_count >= self.policy.max_reminders_before_escalation:
            return AgentAction.ESCALATE_TO_STAFF
        
        # Check if recently contacted (respect rate limiting)
        if case.last_contacted:
            days_since_contact = (today - case.last_contacted).days
            if days_since_contact < self.policy.reminder_interval_days:
                return AgentAction.DO_NOTHING
        
        # For new or pending cases, send reminder
        if case.status in [CaseStatus.PENDING, CaseStatus.AWAITING_REPLY]:
            return AgentAction.SEND_REMINDER
        
        # Default: do nothing if case is already handled
        return AgentAction.DO_NOTHING

    def _execute_action(
        self, case: FollowUpCase, action: AgentAction, today: date
    ) -> None:
        """
        Execute a decided action for a case.
        
        This is where the agent's decisions become concrete actions
        in the real world (sending messages, booking appointments, etc.).
        
        Args:
            case: Follow-up case
            action: Action to execute
            today: Current date
        """
        patient = case.patient
        
        # Log the decision
        rationale = f"Action: {action.value} for {patient.name} " \
                   f"(Urgency: {case.urgency.value}, Status: {case.status.value})"
        self.audit_logger.log_decision(case, action, rationale)
        
        if action == AgentAction.SEND_REMINDER:
            # Compose personalized message
            message_type = "urgent" if case.urgency.value == "critical" else "initial"
            message = self.message_composer.compose(case, message_type)
            
            # Send via preferred channel
            channel = self.notification_channels.get(patient.preferred_channel)
            if channel:
                success = channel.send(patient, message)
                
                # Log communication
                self.audit_logger.log_communication(
                    case, patient.preferred_channel.value, message, 
                    "outbound", success
                )
                
                if success:
                    case.status = CaseStatus.MESSAGE_SENT
                    case.last_contacted = today
                    case.reminder_count += 1
                    case.add_to_log(f"Sent reminder via {patient.preferred_channel.value}")
                    self.data_store.update_last_contacted(patient.patient_id, today)
                    
                    print(f"   ✉️  Sent reminder to {patient.name} via "
                          f"{patient.preferred_channel.value}")
        
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
                
                # Send message
                channel = self.notification_channels.get(patient.preferred_channel)
                if channel:
                    success = channel.send(patient, message)
                    
                    if success:
                        case.status = CaseStatus.AWAITING_REPLY
                        case.add_to_log(f"Proposed {len(available_slots)} appointment slots")
                        print(f"   📅 Proposed appointment slots to {patient.name}")
        
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
                
                # Send confirmation message
                confirmation_msg = f"Your appointment is confirmed for {booked_date.strftime('%A, %B %d')}. See you then!"
                channel = self.notification_channels.get(patient.preferred_channel)
                if channel:
                    channel.send(patient, confirmation_msg)
        
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
        channel = self.notification_channels.get(case.patient.preferred_channel)
        if channel and response:
            success = channel.send(case.patient, response)
            
            if success:
                case.add_to_log(f"Agent: {response}")
                self.audit_logger.log_communication(
                    case, case.patient.preferred_channel.value, response,
                    "outbound", True
                )
                print(f"   💬 Sent response: \"{response[:60]}...\"")
        
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
