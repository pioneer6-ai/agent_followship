"""
Configuration settings for the Patient Follow-up Agent.

This module contains clinic-specific policies and operational parameters
that can be adjusted without modifying the core agent logic.
"""

from dataclasses import dataclass


@dataclass
class ClinicPolicyConfig:
    """
    Configuration for clinic-specific business rules and operational policies.
    
    These settings allow the clinic to customize the agent's behavior
    to match their operational hours, patient communication preferences,
    and escalation thresholds.
    
    Attributes:
        working_hours: Tuple of (start_hour, end_hour) in 24-hour format
        max_reminders_before_escalation: Number of unanswered reminders before escalating to staff
        opt_out_respected: Whether to honor patient opt-out requests
        high_urgency_threshold_days: Days overdue before case becomes HIGH urgency
        critical_urgency_threshold_days: Days overdue before case becomes CRITICAL urgency
        reminder_interval_days: Minimum days to wait between reminder attempts
    """
    working_hours: tuple[int, int] = (9, 18)  # 9 AM to 6 PM
    max_reminders_before_escalation: int = 3
    opt_out_respected: bool = True
    high_urgency_threshold_days: int = 30
    critical_urgency_threshold_days: int = 60
    reminder_interval_days: int = 7
