"""
Action execution handlers for the Patient Follow-up Agent.

This module implements the concrete actions that the agent can take,
including appointment scheduling, escalation to human staff, and
comprehensive audit logging for compliance and accountability.
"""

from datetime import date, datetime
from typing import Callable, Optional
import json
from pathlib import Path

from core.models import FollowUpCase, CaseStatus
from core.actions import AgentAction
from core.data_access import CalendarIntegration


class AppointmentScheduler:
    """
    Handles appointment booking and rescheduling operations.

    This component bridges the agent's decision-making with the
    actual calendar system, converting intent into concrete appointments.
    """

    def __init__(self, calendar: CalendarIntegration):
        """
        Initialize scheduler with calendar integration.

        Args:
            calendar: Calendar system integration instance
        """
        self.calendar = calendar

    def try_book(
        self, case: FollowUpCase, preferred_date: Optional[date] = None
    ) -> tuple[bool, Optional[date]]:
        """
        Attempt to book an appointment for a patient.

        If a preferred date is provided, tries to book that specific date.
        Otherwise, finds the next available slot.

        Args:
            case: Follow-up case for the patient
            preferred_date: Patient's preferred appointment date (optional)

        Returns:
            Tuple of (success boolean, booked date if successful)
        """
        patient = case.patient
        treatment_type = patient.treatment_type

        if preferred_date:
            # Try to book the specific preferred date
            success = self.calendar.book_appointment(
                patient.patient_id, preferred_date, treatment_type
            )
            if success:
                case.status = CaseStatus.BOOKED
                return True, preferred_date
            else:
                return False, None
        else:
            # Find next available slot
            available_slots = self.calendar.find_available_slots(
                treatment_type, after=date.today(), limit=1
            )

            if available_slots:
                next_slot = available_slots[0]
                success = self.calendar.book_appointment(
                    patient.patient_id, next_slot, treatment_type
                )
                if success:
                    case.status = CaseStatus.BOOKED
                    return True, next_slot

            return False, None

    def reschedule(
        self, case: FollowUpCase, old_date: date, new_date: date
    ) -> bool:
        """
        Reschedule an existing appointment to a new date.

        Args:
            case: Follow-up case for the patient
            old_date: Current appointment date
            new_date: New desired appointment date

        Returns:
            True if rescheduling successful
        """
        patient = case.patient

        # Cancel old appointment
        cancel_success = self.calendar.cancel_appointment(
            patient.patient_id, old_date
        )

        if not cancel_success:
            return False

        # Book new appointment
        book_success = self.calendar.book_appointment(
            patient.patient_id, new_date, patient.treatment_type
        )

        if book_success:
            case.status = CaseStatus.BOOKED
            return True
        else:
            # If new booking fails, try to restore old appointment
            self.calendar.book_appointment(
                patient.patient_id, old_date, patient.treatment_type
            )
            return False

    def find_available_slots(
        self, case: FollowUpCase, after: date, limit: int = 5
    ) -> list[date]:
        """
        Find available appointment slots for a patient.

        Args:
            case: Follow-up case
            after: Find slots after this date
            limit: Maximum number of slots to return

        Returns:
            List of available dates
        """
        return self.calendar.find_available_slots(
            case.patient.treatment_type, after=after, limit=limit
        )


class EscalationHandler:
    """
    Manages escalation of cases to human staff.

    This component is crucial for demonstrating the agent's self-awareness
    of its limitations - a key feature for healthcare AI systems where
    knowing when NOT to act autonomously is as important as acting.

    Escalation reasons include:
    - Complex patient questions requiring clinical expertise
    - Multiple failed contact attempts
    - Critical urgency cases
    - Confused or frustrated patients
    """

    def __init__(
        self,
        alert_email: Optional[str] = None,
        notifier: Optional[Callable[[dict], None]] = None,
    ):
        """
        Initialize escalation handler.

        Args:
            alert_email: Email address for escalation alerts
            notifier: Callable invoked with the escalation record so staff are
                actually told. Kept as an injected callable so this class stays
                transport-agnostic and testable.
        """
        self.alert_email = alert_email
        self.notifier = notifier
        self.escalated_cases: list[dict] = []

    def escalate(
        self, case: FollowUpCase, reason: str, priority: str = "normal"
    ) -> None:
        """
        Escalate a case to human staff for manual handling.

        Creates an escalation record and notifies appropriate staff members.
        In production, this would integrate with ticketing systems, send
        alerts via email/SMS, or create tasks in the clinic's workflow system.

        Args:
            case: Follow-up case to escalate
            reason: Human-readable reason for escalation
            priority: Escalation priority ("low", "normal", "high", "critical")
        """
        # Update case status
        case.status = CaseStatus.ESCALATED

        # Create escalation record
        escalation = {
            "patient_id": case.patient.patient_id,
            "patient_name": case.patient.name,
            "treatment_type": case.patient.treatment_type,
            "days_overdue": case.days_overdue,
            "urgency": case.urgency.value,
            "reason": reason,
            "priority": priority,
            "escalated_at": datetime.now().isoformat(),
            "conversation_log": case.conversation_log.copy(),
        }

        self.escalated_cases.append(escalation)

        # Log escalation
        case.add_to_log(f"ESCALATED: {reason} (Priority: {priority})")

        # Tell a human. A failed alert must never abort the cycle -- the case is
        # already recorded above -- but it is loud, because an escalation nobody
        # hears about is not an escalation.
        if self.notifier is not None:
            try:
                self.notifier(escalation)
                case.add_to_log("Escalation alert sent to staff")
            except Exception as exc:  # pragma: no cover - defensive
                case.add_to_log(f"Escalation alert FAILED: {exc}")

        print(f"\n{'='*60}")
        print(f"⚠️  CASE ESCALATED - Priority: {priority.upper()}")
        print(f"{'='*60}")
        print(f"Patient: {case.patient.name} (ID: {case.patient.patient_id})")
        print(f"Reason: {reason}")
        print(f"Urgency: {case.urgency.value}")
        print(f"Days Overdue: {case.days_overdue}")
        print(f"{'='*60}\n")

    def get_escalated_cases(self, priority: Optional[str] = None) -> list[dict]:
        """
        Retrieve escalated cases, optionally filtered by priority.

        Args:
            priority: Filter by priority level (optional)

        Returns:
            List of escalation records
        """
        if priority:
            return [
                case for case in self.escalated_cases
                if case["priority"] == priority
            ]
        return self.escalated_cases.copy()

    def resolve_escalation(self, patient_id: str, resolution_note: str) -> bool:
        """
        Mark an escalated case as resolved by staff.

        Args:
            patient_id: Patient identifier
            resolution_note: Notes on how the case was resolved

        Returns:
            True if escalation found and marked resolved
        """
        for escalation in self.escalated_cases:
            if escalation["patient_id"] == patient_id:
                escalation["resolved_at"] = datetime.now().isoformat()
                escalation["resolution_note"] = resolution_note
                return True
        return False


class AuditLogger:
    """
    Comprehensive audit logging for compliance and accountability.

    In healthcare applications, maintaining a complete audit trail is
    essential for:
    - Regulatory compliance (HIPAA, PDPA, GDPR)
    - Quality assurance and system improvement
    - Liability protection
    - Understanding agent decision-making

    Every agent decision and action is logged with full context.
    """

    def __init__(self, log_file: Optional[str] = None):
        """
        Initialize audit logger.

        Args:
            log_file: Path to audit log file (optional, defaults to audit_log.json)
        """
        self.log_file = log_file or "audit_log.json"
        self.log_entries: list[dict] = []
        self._load_existing_logs()

    def _load_existing_logs(self) -> None:
        """Load existing audit logs from file if present."""
        log_path = Path(self.log_file)
        if log_path.exists():
            try:
                with open(log_path, 'r') as f:
                    self.log_entries = json.load(f)
            except json.JSONDecodeError:
                # If file is corrupted, start fresh
                self.log_entries = []

    def _save_logs(self) -> None:
        """Persist audit logs to file."""
        log_path = Path(self.log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        with open(log_path, 'w') as f:
            json.dump(self.log_entries, f, indent=2, default=str)

    def log_decision(
        self, case: FollowUpCase, action: AgentAction, rationale: str,
        additional_context: Optional[dict] = None
    ) -> None:
        """
        Log an agent decision with full context and rationale.

        This creates a transparent, auditable record of why the agent
        took a particular action, which is crucial for healthcare AI.

        Args:
            case: Follow-up case being processed
            action: Action the agent decided to take
            rationale: Human-readable explanation of the decision
            additional_context: Optional additional context information
        """
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "event_type": "agent_decision",
            "patient_id": case.patient.patient_id,
            "patient_name": case.patient.name,
            "case_status": case.status.value,
            "urgency": case.urgency.value,
            "days_overdue": case.days_overdue,
            "action_taken": action.value,
            "rationale": rationale,
            "reminder_count": case.reminder_count,
            "conversation_length": len(case.conversation_log),
        }

        if additional_context:
            log_entry["additional_context"] = additional_context

        self.log_entries.append(log_entry)
        self._save_logs()

    def log_communication(
        self, case: FollowUpCase, channel: str, message: str,
        direction: str, success: bool
    ) -> None:
        """
        Log a communication event (sent or received message).

        Args:
            case: Follow-up case
            channel: Communication channel used
            message: Message content
            direction: "outbound" or "inbound"
            success: Whether communication was successful
        """
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "event_type": "communication",
            "patient_id": case.patient.patient_id,
            "channel": channel,
            "direction": direction,
            "success": success,
            "message_preview": message[:100] + "..." if len(message) > 100 else message,
        }

        self.log_entries.append(log_entry)
        self._save_logs()

    def log_appointment_action(
        self, case: FollowUpCase, action: str, appointment_date: Optional[date],
        success: bool, details: Optional[str] = None
    ) -> None:
        """
        Log appointment-related actions (booking, rescheduling, cancellation).

        Args:
            case: Follow-up case
            action: Type of appointment action
            appointment_date: Date of appointment
            success: Whether action was successful
            details: Optional additional details
        """
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "event_type": "appointment_action",
            "patient_id": case.patient.patient_id,
            "action": action,
            "appointment_date": appointment_date.isoformat() if appointment_date else None,
            "success": success,
            "treatment_type": case.patient.treatment_type,
        }

        if details:
            log_entry["details"] = details

        self.log_entries.append(log_entry)
        self._save_logs()

    def get_patient_history(self, patient_id: str) -> list[dict]:
        """
        Retrieve complete audit history for a specific patient.

        Args:
            patient_id: Patient identifier

        Returns:
            List of audit log entries for this patient
        """
        return [
            entry for entry in self.log_entries
            if entry.get("patient_id") == patient_id
        ]

    def get_logs_by_date_range(
        self, start_date: datetime, end_date: datetime
    ) -> list[dict]:
        """
        Retrieve audit logs within a date range.

        Args:
            start_date: Start of date range
            end_date: End of date range

        Returns:
            List of audit log entries in range
        """
        return [
            entry for entry in self.log_entries
            if start_date <= datetime.fromisoformat(entry["timestamp"]) <= end_date
        ]

    def generate_summary_report(self) -> dict:
        """
        Generate summary statistics from audit logs.

        Returns:
            Dictionary containing summary metrics
        """
        total_decisions = sum(
            1 for entry in self.log_entries
            if entry["event_type"] == "agent_decision"
        )

        total_communications = sum(
            1 for entry in self.log_entries
            if entry["event_type"] == "communication"
        )

        successful_bookings = sum(
            1 for entry in self.log_entries
            if entry["event_type"] == "appointment_action"
            and entry["action"] == "booking"
            and entry["success"]
        )

        return {
            "total_decisions": total_decisions,
            "total_communications": total_communications,
            "successful_bookings": successful_bookings,
            "total_log_entries": len(self.log_entries),
        }
