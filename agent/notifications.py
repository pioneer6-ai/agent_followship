"""
Notification and messaging layer for patient communication.

This module provides multi-channel notification capabilities,
allowing the agent to reach patients through their preferred
communication method (SMS, WhatsApp, Email, Phone).
"""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional

from core.models import PatientRecord, ContactChannel, FollowUpCase


class NotificationChannel(ABC):
    """
    Abstract base class for all notification channels.
    
    Each channel implementation handles the specifics of sending
    messages through that medium (API calls, formatting requirements, etc.).
    """

    @abstractmethod
    def send(self, patient: PatientRecord, message: str) -> bool:
        """
        Send a message to a patient through this channel.
        
        Args:
            patient: Patient to contact
            message: Message content to send
            
        Returns:
            True if message was sent successfully, False otherwise
        """
        pass

    @abstractmethod
    def get_channel_type(self) -> ContactChannel:
        """
        Get the channel type identifier.
        
        Returns:
            ContactChannel enum value
        """
        pass


class SMSChannel(NotificationChannel):
    """
    SMS notification channel implementation.
    
    In production, this would integrate with SMS gateway services
    like Twilio, AWS SNS, or similar providers.
    """

    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize SMS channel.
        
        Args:
            api_key: API key for SMS service (e.g., Twilio)
        """
        self.api_key = api_key

    def send(self, patient: PatientRecord, message: str) -> bool:
        """Send SMS message to patient."""
        phone_number = patient.contact_info.get(ContactChannel.SMS)
        
        if not phone_number:
            print(f"[SMS] No phone number for patient {patient.patient_id}")
            return False
        
        # Mock implementation - in production, would call SMS API
        print(f"[SMS] Sending to {phone_number}:")
        print(f"      {message}")
        
        # Simulate successful send
        return True

    def get_channel_type(self) -> ContactChannel:
        """Return SMS channel type."""
        return ContactChannel.SMS


class WhatsAppChannel(NotificationChannel):
    """
    WhatsApp notification channel implementation.
    
    In production, this would integrate with WhatsApp Business API
    or services like Twilio WhatsApp, MessageBird, etc.
    """

    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize WhatsApp channel.
        
        Args:
            api_key: API key for WhatsApp service
        """
        self.api_key = api_key

    def send(self, patient: PatientRecord, message: str) -> bool:
        """Send WhatsApp message to patient."""
        whatsapp_number = patient.contact_info.get(ContactChannel.WHATSAPP)
        
        if not whatsapp_number:
            print(f"[WhatsApp] No WhatsApp number for patient {patient.patient_id}")
            return False
        
        # Mock implementation - in production, would call WhatsApp API
        print(f"[WhatsApp] Sending to {whatsapp_number}:")
        print(f"          {message}")
        
        # Simulate successful send
        return True

    def get_channel_type(self) -> ContactChannel:
        """Return WhatsApp channel type."""
        return ContactChannel.WHATSAPP


class EmailChannel(NotificationChannel):
    """
    Email notification channel implementation.
    
    In production, this would integrate with email services
    like SendGrid, AWS SES, Mailgun, or SMTP servers.
    """

    def __init__(self, smtp_config: Optional[dict] = None):
        """
        Initialize email channel.
        
        Args:
            smtp_config: SMTP server configuration
        """
        self.smtp_config = smtp_config or {}

    def send(self, patient: PatientRecord, message: str) -> bool:
        """Send email to patient."""
        email = patient.contact_info.get(ContactChannel.EMAIL)
        
        if not email:
            print(f"[Email] No email address for patient {patient.patient_id}")
            return False
        
        # Mock implementation - in production, would send actual email
        print(f"[Email] Sending to {email}:")
        print(f"       Subject: Dental Appointment Reminder")
        print(f"       {message}")
        
        # Simulate successful send
        return True

    def get_channel_type(self) -> ContactChannel:
        """Return Email channel type."""
        return ContactChannel.EMAIL


class PhoneCallChannel(NotificationChannel):
    """
    Phone call notification channel implementation.
    
    In production, this would integrate with voice services
    like Twilio Voice, Amazon Connect, or trigger manual calls.
    """

    def __init__(self, voice_api_key: Optional[str] = None):
        """
        Initialize phone call channel.
        
        Args:
            voice_api_key: API key for voice service
        """
        self.voice_api_key = voice_api_key

    def send(self, patient: PatientRecord, message: str) -> bool:
        """Initiate phone call to patient."""
        phone_number = patient.contact_info.get(ContactChannel.PHONE_CALL)
        
        if not phone_number:
            print(f"[Phone] No phone number for patient {patient.patient_id}")
            return False
        
        # Mock implementation - in production, would initiate automated call
        print(f"[Phone] Calling {phone_number}:")
        print(f"       (Automated voice message): {message}")
        
        # Simulate successful call
        return True

    def get_channel_type(self) -> ContactChannel:
        """Return Phone channel type."""
        return ContactChannel.PHONE_CALL


class MessageComposerAgent:
    """
    AI-powered message composition agent.
    
    This component generates personalized, context-aware messages
    for patients based on their profile, language preference,
    urgency level, and treatment type.
    
    In production, this would use an LLM (e.g., OpenAI GPT, Claude)
    to generate natural, empathetic messages. For this demo, it uses
    template-based generation.
    """

    def __init__(self, use_llm: bool = False, llm_api_key: Optional[str] = None):
        """
        Initialize message composer.
        
        Args:
            use_llm: Whether to use LLM for message generation
            llm_api_key: API key for LLM service (if use_llm=True)
        """
        self.use_llm = use_llm
        self.llm_api_key = llm_api_key

    def compose(self, case: FollowUpCase, message_type: str = "initial") -> str:
        """
        Generate a personalized reminder message for a patient.
        
        The message is tailored based on:
        - Patient's preferred language
        - Urgency level
        - Treatment type
        - Communication style (formal vs. friendly)
        
        Args:
            case: Follow-up case containing patient information
            message_type: Type of message ("initial", "reminder", "urgent")
            
        Returns:
            Composed message string
        """
        if self.use_llm:
            return self._compose_with_llm(case, message_type)
        else:
            return self._compose_with_template(case, message_type)

    def _compose_with_template(self, case: FollowUpCase, message_type: str) -> str:
        """
        Generate message using template-based approach.
        
        This is a deterministic, rule-based method that ensures
        consistency and compliance while still personalizing content.
        """
        patient = case.patient
        urgency = case.urgency.value
        treatment = case.patient.treatment_type.replace("_", " ").title()
        
        # Select greeting based on time and formality
        greeting = f"Hello {patient.name},"
        
        # Compose main message based on urgency and type
        if message_type == "initial":
            if urgency == "critical":
                body = (
                    f"This is an important reminder about your {treatment.lower()} follow-up. "
                    f"It's been {case.days_overdue} days past your recommended appointment date. "
                    f"Please contact us as soon as possible to schedule your visit. "
                    f"Your dental health is important to us."
                )
            elif urgency == "high":
                body = (
                    f"We noticed you're overdue for your {treatment.lower()} appointment. "
                    f"To maintain your dental health, we recommend scheduling soon. "
                    f"Would you like to book an appointment this week?"
                )
            else:
                body = (
                    f"It's time for your {treatment.lower()} appointment! "
                    f"We'd love to see you soon. "
                    f"Reply with your preferred day and we'll find a time that works."
                )
        elif message_type == "reminder":
            body = (
                f"Following up on our previous message about your {treatment.lower()} appointment. "
                f"We have several time slots available. "
                f"Would you like to schedule a visit?"
            )
        else:  # urgent
            body = (
                f"We're concerned about your overdue {treatment.lower()} appointment. "
                f"Please contact us at your earliest convenience. "
                f"Our team is ready to help you maintain your dental health."
            )
        
        # Add call-to-action
        cta = "Reply to this message or call us to book your appointment."
        
        # Compose complete message
        message = f"{greeting}\n\n{body}\n\n{cta}\n\nBest regards,\nYour Dental Care Team"
        
        return message

    def _compose_with_llm(self, case: FollowUpCase, message_type: str) -> str:
        """
        Generate message using LLM (placeholder for future implementation).
        
        This would send a prompt to an LLM service with context about
        the patient and case, receiving a personalized message in return.
        """
        # Placeholder for LLM integration
        # In production, would call OpenAI API, Claude API, etc.
        
        prompt = f"""
        Generate a friendly, professional reminder message for a dental patient with these details:
        - Patient name: {case.patient.name}
        - Treatment type: {case.patient.treatment_type}
        - Days overdue: {case.days_overdue}
        - Urgency: {case.urgency.value}
        - Language: {case.patient.language}
        - Message type: {message_type}
        
        Keep the message concise (under 160 characters for SMS compatibility),
        warm but professional, and include a clear call-to-action.
        """
        
        # For demo, fall back to template
        return self._compose_with_template(case, message_type)

    def compose_slot_proposal(self, case: FollowUpCase, available_slots: list) -> str:
        """
        Generate a message proposing available appointment slots.
        
        Args:
            case: Follow-up case
            available_slots: List of available dates
            
        Returns:
            Message with slot options
        """
        patient = case.patient
        treatment = case.patient.treatment_type.replace("_", " ").title()
        
        slots_text = "\n".join([
            f"{i+1}. {slot.strftime('%A, %B %d')}"
            for i, slot in enumerate(available_slots[:3])
        ])
        
        message = f"""Hello {patient.name},

We have the following times available for your {treatment.lower()} appointment:

{slots_text}

Please reply with the number of your preferred time, or suggest an alternative.

Best regards,
Your Dental Care Team"""
        
        return message
