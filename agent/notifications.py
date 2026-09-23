"""
Notification and messaging layer for patient communication.

This module provides multi-channel notification capabilities,
allowing the agent to reach patients through their preferred
communication method (SMS, WhatsApp, Email, Phone).

Every channel reports a :class:`~agent.delivery.NotificationOutcome` rather than a
bare ``bool``, so the agent can tell *why* a send failed and what to try instead.
Transmission itself is delegated to a :class:`~agent.delivery.DeliveryBackend`,
which keeps the offline demo and the live provider paths behind one interface.
"""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional

from agent.delivery import (
    DeliveryBackend,
    NotificationOutcome,
    PrintDeliveryBackend,
    missing_recipient_outcome,
)
from core.models import PatientRecord, ContactChannel, FollowUpCase


class NotificationChannel(ABC):
    """
    Abstract base class for all notification channels.

    Each channel implementation handles the specifics of sending
    messages through that medium (formatting rules, length limits, etc.)
    and delegates the actual transmission to a delivery backend.
    """

    def __init__(self, backend: Optional[DeliveryBackend] = None):
        """
        Args:
            backend: Transmission strategy. Defaults to the offline printing
                backend so a channel never sends anything unless one is supplied.
        """
        self.backend: DeliveryBackend = backend or PrintDeliveryBackend()

    @abstractmethod
    def send(self, patient: PatientRecord, message: str) -> NotificationOutcome:
        """
        Send a message to a patient through this channel.

        Args:
            patient: Patient to contact
            message: Message content to send

        Returns:
            The outcome of the attempt. Failures are reported, never raised, so
            the agent can react to them.
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

    def _recipient_for(self, patient: PatientRecord) -> str:
        """
        Address to reach this patient on, using this channel.

        Falls back to the preferred channel's address when this channel has none
        of its own (a patient with a single phone number recorded as
        ``PHONE_CALL`` is still reachable by SMS).
        """
        direct = patient.contact_info.get(self.get_channel_type())
        if direct:
            return str(direct)
        fallback = patient.contact_info.get(patient.preferred_channel)
        return str(fallback) if fallback else ""


class SMSChannel(NotificationChannel):
    """
    SMS notification channel implementation.

    Delegates to the ``send_sms_message`` tool (Twilio), which handles E.164
    normalization, allow-listing and dry-run.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        backend: Optional[DeliveryBackend] = None,
    ):
        """
        Initialize SMS channel.

        Args:
            api_key: API key for SMS service (retained for callers that still
                pass one; the tool layer reads its own credentials from the
                environment).
            backend: Transmission strategy.
        """
        super().__init__(backend=backend)
        self.api_key = api_key

    def send(self, patient: PatientRecord, message: str) -> NotificationOutcome:
        """Send SMS message to patient, reporting the outcome instead of raising."""
        phone_number = self._recipient_for(patient)

        if not phone_number:
            print(f"[SMS] No phone number for patient {patient.patient_id}")
            return missing_recipient_outcome(ContactChannel.SMS)

        return self.backend.deliver(ContactChannel.SMS, phone_number, message)

    def get_channel_type(self) -> ContactChannel:
        """Return SMS channel type."""
        return ContactChannel.SMS


class WhatsAppChannel(NotificationChannel):
    """
    WhatsApp notification channel implementation.

    Delegates to the ``send_whatsapp_message`` tool (Meta Cloud API or Twilio),
    which handles the business-initiated template rules.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        backend: Optional[DeliveryBackend] = None,
    ):
        """
        Initialize WhatsApp channel.

        Args:
            api_key: API key for WhatsApp service (see :class:`SMSChannel`).
            backend: Transmission strategy.
        """
        super().__init__(backend=backend)
        self.api_key = api_key

    def send(self, patient: PatientRecord, message: str) -> NotificationOutcome:
        """Send WhatsApp message to patient, reporting the outcome instead of raising."""
        whatsapp_number = self._recipient_for(patient)

        if not whatsapp_number:
            print(f"[WhatsApp] No WhatsApp number for patient {patient.patient_id}")
            return missing_recipient_outcome(ContactChannel.WHATSAPP)

        return self.backend.deliver(
            ContactChannel.WHATSAPP, whatsapp_number, message
        )

    def get_channel_type(self) -> ContactChannel:
        """Return WhatsApp channel type."""
        return ContactChannel.WHATSAPP


class EmailChannel(NotificationChannel):
    """
    Email notification channel implementation.

    Delegates to the ``send_email_message`` tool (SMTP), which is the path that
    actually reaches an inbox when no sending domain is available.
    """

    def __init__(
        self,
        smtp_config: Optional[dict] = None,
        backend: Optional[DeliveryBackend] = None,
    ):
        """
        Initialize email channel.

        Args:
            smtp_config: SMTP server configuration (retained for callers that
                still pass one; the tool layer reads its own from the environment).
            backend: Transmission strategy.
        """
        super().__init__(backend=backend)
        self.smtp_config = smtp_config or {}

    def send(self, patient: PatientRecord, message: str) -> NotificationOutcome:
        """Send email to patient, reporting the outcome instead of raising."""
        email = self._recipient_for(patient)

        if not email:
            print(f"[Email] No email address for patient {patient.patient_id}")
            return missing_recipient_outcome(ContactChannel.EMAIL)

        return self.backend.deliver(
            ContactChannel.EMAIL,
            email,
            message,
            subject="Dental Appointment Reminder",
        )

    def get_channel_type(self) -> ContactChannel:
        """Return Email channel type."""
        return ContactChannel.EMAIL


class PhoneCallChannel(NotificationChannel):
    """
    Phone call notification channel implementation.

    No automated voice sender exists in the tool layer, so this channel always
    reports ``unsupported_channel``. That is deliberate: an attempt is an
    observed failure the agent can route around, which is better than a silent
    "no phone calls configured" that leaves the case stuck.
    """

    def __init__(
        self,
        voice_api_key: Optional[str] = None,
        backend: Optional[DeliveryBackend] = None,
    ):
        """
        Initialize phone call channel.

        Args:
            voice_api_key: API key for voice service.
            backend: Transmission strategy.
        """
        super().__init__(backend=backend)
        self.voice_api_key = voice_api_key

    def send(self, patient: PatientRecord, message: str) -> NotificationOutcome:
        """Report that an automated call cannot be placed, with fallbacks."""
        phone_number = self._recipient_for(patient)

        if not phone_number:
            print(f"[Phone] No phone number for patient {patient.patient_id}")
            return missing_recipient_outcome(ContactChannel.PHONE_CALL)

        return self.backend.deliver(ContactChannel.PHONE_CALL, phone_number, message)

    def get_channel_type(self) -> ContactChannel:
        """Return Phone channel type."""
        return ContactChannel.PHONE_CALL


def build_notification_channels(
    backend: Optional[DeliveryBackend] = None,
    *,
    force_backend: Optional[DeliveryBackend] = None,
) -> "dict[ContactChannel, NotificationChannel]":
    """
    Build the channel set the orchestrator sends through.

    Chooses the transmission strategy in one place so no caller has to decide:

    * an explicit ``force_backend`` (tests) always wins;
    * otherwise ``backend`` when one is given;
    * otherwise the real toolkit backend when ``MESSAGING_DRY_RUN=0`` asks for
      live sends;
    * otherwise the offline printing backend, which is the default so a
      populated ``.env`` alone can never message a real patient.

    Args:
        backend: Backend to use when none is forced.
        force_backend: Backend that overrides everything.

    Returns:
        Mapping of :class:`~core.models.ContactChannel` to channel instance.
    """
    chosen = force_backend if force_backend is not None else backend
    if chosen is None:
        from agent.delivery import ToolkitDeliveryBackend, is_configured_for_live_sends

        if is_configured_for_live_sends():
            chosen = ToolkitDeliveryBackend.from_environment()
        else:
            chosen = PrintDeliveryBackend()

    return {
        ContactChannel.SMS: SMSChannel(backend=chosen),
        ContactChannel.WHATSAPP: WhatsAppChannel(backend=chosen),
        ContactChannel.EMAIL: EmailChannel(backend=chosen),
        ContactChannel.PHONE_CALL: PhoneCallChannel(backend=chosen),
    }


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
