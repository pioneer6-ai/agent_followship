"""
Conversation management and intent recognition for patient interactions.

This module handles multi-turn conversations with patients, understanding
their intent and deciding on appropriate follow-up actions.
"""

import re
from datetime import datetime, date
from typing import Optional, Tuple

from core.models import FollowUpCase, CaseStatus
from core.actions import AgentAction


class ConversationManager:
    """
    Manages multi-turn conversations with patients.
    
    This is the core "autonomous decision-making" component of the agent.
    It analyzes patient replies, recognizes intent, and determines the
    next action to take - demonstrating the agent's ability to handle
    dynamic, unscripted interactions.
    
    Key capabilities:
    - Intent recognition (booking, declining, rescheduling, questions)
    - Context tracking across conversation turns
    - Escalation detection (when human intervention is needed)
    """

    def __init__(self, use_llm: bool = False):
        """
        Initialize conversation manager.
        
        Args:
            use_llm: Whether to use LLM for intent recognition (vs. rule-based)
        """
        self.use_llm = use_llm

    def handle_reply(
        self, case: FollowUpCase, incoming_message: str
    ) -> Tuple[AgentAction, Optional[dict]]:
        """
        Process a patient's reply and determine the next action.
        
        This is the heart of the autonomous agent - it "observes" the patient's
        response and "decides" what to do next without human intervention.
        
        Args:
            case: Current follow-up case
            incoming_message: Patient's message text
            
        Returns:
            Tuple of (AgentAction to take, Optional context dictionary)
        """
        # Log the incoming message
        case.add_to_log(f"Patient: {incoming_message}")
        
        # Normalize message for analysis
        message_lower = incoming_message.lower().strip()
        
        # Recognize intent and decide action
        intent, context = self._recognize_intent(message_lower)
        
        # Map intent to agent action
        action = self._intent_to_action(intent, case, context)
        
        # Log the decision
        case.add_to_log(f"Agent Decision: {action.value} (Intent: {intent})")
        
        return action, context

    def _recognize_intent(self, message: str) -> Tuple[str, dict]:
        """
        Recognize the patient's intent from their message.
        
        Intent categories:
        - confirm_booking: Patient agrees to schedule appointment
        - decline: Patient doesn't want appointment now
        - reschedule: Patient wants different time
        - ask_question: Patient has questions
        - unclear: Cannot determine intent (needs escalation)
        
        Args:
            message: Normalized patient message
            
        Returns:
            Tuple of (intent string, context dictionary)
        """
        context = {}
        
        # Check for booking confirmation patterns
        confirm_patterns = [
            r'\b(yes|yeah|sure|ok|okay|sounds good|that works|perfect)\b',
            r'\b(book|schedule|confirm|i.ll take|i want)\b',
            r'\b([1-3])\b',  # Selecting a slot number
        ]
        
        if any(re.search(pattern, message) for pattern in confirm_patterns):
            # Check if they specified a slot number
            slot_match = re.search(r'\b([1-3])\b', message)
            if slot_match:
                context['selected_slot'] = int(slot_match.group(1))
            return "confirm_booking", context
        
        # Check for decline patterns
        decline_patterns = [
            r'\b(no|not now|maybe later|cancel|don.t need|not interested)\b',
            r'\b(busy|away|traveling|out of town)\b',
        ]
        
        if any(re.search(pattern, message) for pattern in decline_patterns):
            return "decline", context
        
        # Check for reschedule patterns
        reschedule_patterns = [
            r'\b(different time|another day|reschedule|change)\b',
            r'\b(next week|next month|later)\b',
            r'\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b',
        ]
        
        if any(re.search(pattern, message) for pattern in reschedule_patterns):
            # Try to extract preferred day
            days = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
            for day in days:
                if day in message:
                    context['preferred_day'] = day
                    break
            return "reschedule", context
        
        # Check for questions
        question_patterns = [
            r'\?',
            r'\b(what|when|where|how|why|who)\b',
            r'\b(cost|price|insurance|covered)\b',
            r'\b(question|ask|wondering|curious)\b',
        ]
        
        if any(re.search(pattern, message) for pattern in question_patterns):
            context['question_text'] = message
            return "ask_question", context
        
        # If none of the above, intent is unclear
        return "unclear", context

    def _intent_to_action(
        self, intent: str, case: FollowUpCase, context: dict
    ) -> AgentAction:
        """
        Map recognized intent to concrete agent action.
        
        This decision logic demonstrates the agent's autonomous reasoning:
        it considers the intent, current case status, and context to
        choose the most appropriate action.
        
        Args:
            intent: Recognized intent
            case: Current follow-up case
            context: Additional context from intent recognition
            
        Returns:
            AgentAction to execute
        """
        if intent == "confirm_booking":
            # Patient wants to book - proceed with confirmation
            case.status = CaseStatus.BOOKED
            return AgentAction.CONFIRM_BOOKING
        
        elif intent == "decline":
            # Patient declined - mark case and stop contacting
            case.status = CaseStatus.DECLINED
            return AgentAction.MARK_DECLINED
        
        elif intent == "reschedule":
            # Patient wants different time - propose new slots
            case.status = CaseStatus.AWAITING_REPLY
            return AgentAction.PROPOSE_SLOT
        
        elif intent == "ask_question":
            # Patient has questions - this needs human expertise
            # Agent knows its boundaries and escalates appropriately
            case.status = CaseStatus.ESCALATED
            return AgentAction.ESCALATE_TO_STAFF
        
        else:  # unclear
            # Cannot understand intent
            # Check if we've already sent multiple reminders
            if case.reminder_count >= 2:
                # After 2 unclear responses, escalate to human
                case.status = CaseStatus.ESCALATED
                return AgentAction.ESCALATE_TO_STAFF
            else:
                # Try sending another reminder with clearer options
                return AgentAction.SEND_REMINDER

    def generate_response(
        self, action: AgentAction, case: FollowUpCase, context: dict
    ) -> str:
        """
        Generate appropriate response message based on action.
        
        Args:
            action: Action being taken
            case: Current follow-up case
            context: Context from conversation
            
        Returns:
            Response message to send to patient
        """
        patient_name = case.patient.name
        
        if action == AgentAction.CONFIRM_BOOKING:
            return f"Great, {patient_name}! We've confirmed your appointment. You'll receive a confirmation shortly with the details."
        
        elif action == AgentAction.MARK_DECLINED:
            return f"Understood, {patient_name}. If you'd like to schedule in the future, just let us know. Take care!"
        
        elif action == AgentAction.PROPOSE_SLOT:
            return "Let me check our available times and get back to you with options."
        
        elif action == AgentAction.ESCALATE_TO_STAFF:
            return f"Thank you for your message, {patient_name}. One of our team members will contact you shortly to help with your request."
        
        elif action == AgentAction.SEND_REMINDER:
            return f"Hi {patient_name}, just following up - would you like to schedule your appointment? Please reply 'yes' to book or 'no' if not needed right now."
        
        else:
            return "Thank you for your response."

    def should_escalate(self, case: FollowUpCase) -> bool:
        """
        Determine if a case should be escalated to human staff.
        
        Escalation criteria demonstrate the agent's self-awareness
        of its limitations - a critical feature for healthcare applications.
        
        Escalate when:
        - Too many unanswered reminders
        - Patient asks complex questions
        - Critical urgency with no response
        - Patient seems confused or frustrated
        
        Args:
            case: Follow-up case to evaluate
            
        Returns:
            True if case should be escalated
        """
        # Too many reminders without clear response
        if case.reminder_count >= 3 and case.status == CaseStatus.AWAITING_REPLY:
            return True
        
        # Critical cases need human attention
        if case.urgency.value == "critical" and case.reminder_count >= 1:
            return True
        
        # Check conversation log for confusion indicators
        recent_messages = case.conversation_log[-3:] if case.conversation_log else []
        confusion_keywords = ["don't understand", "confused", "what do you mean", "unclear"]
        
        for msg in recent_messages:
            if any(keyword in msg.lower() for keyword in confusion_keywords):
                return True
        
        return False
