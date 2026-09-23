"""
Configuration settings for the Patient Follow-up Agent.

This module contains clinic-specific policies and operational parameters
that can be adjusted without modifying the core agent logic.
"""

from dataclasses import dataclass
from typing import Mapping, Optional
import os


def _int(source: Mapping[str, str], name: str, default: int) -> int:
    """Parse a positive integer, falling back on blank or garbage."""
    raw = str(source.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


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

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "ClinicPolicyConfig":
        """
        Build the policy from environment variables, keeping the defaults.

        A clinic changes its escalation threshold without editing code, and an
        unset or malformed variable keeps the documented default rather than
        failing startup -- a typo in a tuning knob must not take the agent down.

        Variables::

            AGENT_MAX_REMINDERS_BEFORE_ESCALATION  (int, default 3)
            AGENT_REMINDER_INTERVAL_DAYS           (int, default 7)
            AGENT_HIGH_URGENCY_THRESHOLD_DAYS      (int, default 30)
            AGENT_CRITICAL_URGENCY_THRESHOLD_DAYS  (int, default 60)

        Args:
            env: Mapping to read instead of ``os.environ`` (tests).

        Returns:
            A populated config.
        """
        source: Mapping[str, str] = os.environ if env is None else env
        defaults = cls()
        return cls(
            working_hours=defaults.working_hours,
            max_reminders_before_escalation=_int(
                source,
                "AGENT_MAX_REMINDERS_BEFORE_ESCALATION",
                defaults.max_reminders_before_escalation,
            ),
            opt_out_respected=defaults.opt_out_respected,
            high_urgency_threshold_days=_int(
                source,
                "AGENT_HIGH_URGENCY_THRESHOLD_DAYS",
                defaults.high_urgency_threshold_days,
            ),
            critical_urgency_threshold_days=_int(
                source,
                "AGENT_CRITICAL_URGENCY_THRESHOLD_DAYS",
                defaults.critical_urgency_threshold_days,
            ),
            reminder_interval_days=_int(
                source,
                "AGENT_REMINDER_INTERVAL_DAYS",
                defaults.reminder_interval_days,
            ),
        )
