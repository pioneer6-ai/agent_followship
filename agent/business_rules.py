"""
Core business rule engine for patient follow-up determination.

This module implements the deterministic logic for:
1. Identifying which patients are overdue for follow-up
2. Computing urgency levels based on clinical and operational factors

The rule engine is intentionally deterministic (non-LLM) to provide
transparency and auditability, which is crucial for healthcare applications.
"""

from datetime import date
from typing import Optional

from core.models import PatientRecord, FollowUpCase, UrgencyLevel
from core.config import ClinicPolicyConfig


class RecallRuleEngine:
    """
    Deterministic rule engine for identifying overdue patients.
    
    This class implements transparent, auditable rules to determine
    which patients need follow-up based on their last visit date
    and recommended recall interval.
    
    The separation of deterministic rules from AI-based decision making
    is intentional: it allows clinic staff to understand and validate
    the core logic while still benefiting from AI for communication
    and conversation management.
    """

    def __init__(self, config: ClinicPolicyConfig):
        """
        Initialize the rule engine with clinic policy configuration.
        
        Args:
            config: Clinic-specific policy settings
        """
        self.config = config

    def compute_overdue_patients(
        self, patients: list[PatientRecord], as_of: date
    ) -> list[FollowUpCase]:
        """
        Identify all patients who are overdue for follow-up.
        
        This method applies the recall rules to all active patients
        and generates FollowUpCase objects for those requiring attention.
        
        Args:
            patients: List of all active patients to evaluate
            as_of: Reference date for calculating overdue status (typically today)
            
        Returns:
            List of FollowUpCase objects for patients requiring follow-up
        """
        overdue_cases = []

        for patient in patients:
            days_since_visit = (as_of - patient.last_visit_date).days
            days_overdue = days_since_visit - patient.recall_interval_days

            # Only create case if patient is overdue
            if days_overdue > 0:
                # Generate explanation for why follow-up is needed
                reason = self._generate_reason(patient, days_overdue)
                
                # Create the follow-up case (urgency will be scored separately)
                case = FollowUpCase(
                    patient=patient,
                    days_overdue=days_overdue,
                    urgency=UrgencyLevel.LOW,  # Will be updated by UrgencyScorer
                    reason=reason
                )
                overdue_cases.append(case)

        return overdue_cases

    def _generate_reason(self, patient: PatientRecord, days_overdue: int) -> str:
        """
        Generate human-readable explanation for why follow-up is needed.
        
        This explanation is used for audit trails and can be shown to
        clinic staff for transparency.
        
        Args:
            patient: Patient record
            days_overdue: Number of days past recommended recall date
            
        Returns:
            Human-readable reason string
        """
        return (
            f"Patient {patient.name} (ID: {patient.patient_id}) is {days_overdue} days "
            f"overdue for {patient.treatment_type} follow-up. "
            f"Last visit: {patient.last_visit_date.isoformat()}, "
            f"recommended interval: {patient.recall_interval_days} days."
        )


class UrgencyScorer:
    """
    Computes urgency levels for follow-up cases.
    
    This class implements a scoring algorithm that considers multiple factors:
    - Days overdue (clinical risk increases with delay)
    - Treatment type (some treatments require more timely follow-up)
    - Patient history (no-shows may need different handling)
    - Clinical notes (future enhancement: LLM can analyze notes for risk factors)
    
    The urgency score determines prioritization in the agent workflow.
    """

    def __init__(self, config: ClinicPolicyConfig):
        """
        Initialize the scorer with clinic policy configuration.
        
        Args:
            config: Clinic-specific policy settings including urgency thresholds
        """
        self.config = config
        
        # Treatment type risk weights (some treatments are more time-sensitive)
        self._treatment_weights = {
            "post_surgery": 2.0,      # Surgical follow-up is critical
            "orthodontic_adjustment": 1.5,  # Orthodontic treatment shouldn't be interrupted
            "cavity_treatment": 1.5,  # Dental decay can worsen quickly
            "root_canal_followup": 1.8,  # Endodontic follow-up is important
            "periodontal_maintenance": 1.3,  # Gum disease requires consistent care
            "cleaning": 1.0,          # Routine cleaning is important but less urgent
            "checkup": 0.8,           # General checkup is lowest urgency
        }

    def score(
        self, case: FollowUpCase, clinical_notes: Optional[str] = None
    ) -> UrgencyLevel:
        """
        Calculate urgency level for a follow-up case.
        
        The algorithm uses a point-based system that considers:
        1. Days overdue (base urgency)
        2. Treatment type weight
        3. Patient reliability (no-show history)
        4. Clinical notes (optional AI analysis)
        
        Args:
            case: Follow-up case to score
            clinical_notes: Optional clinical notes for AI-based risk assessment
            
        Returns:
            Computed urgency level (LOW, MEDIUM, HIGH, or CRITICAL)
        """
        days_overdue = case.days_overdue
        
        # Apply treatment type weight
        treatment_weight = self._treatment_weights.get(
            case.patient.treatment_type.lower(), 1.0
        )
        weighted_days = days_overdue * treatment_weight
        
        # Adjust for patient reliability
        # Patients with no-show history might need earlier intervention
        if case.patient.no_show_history > 2:
            weighted_days *= 1.2
        
        # Determine urgency based on weighted overdue days
        if weighted_days >= self.config.critical_urgency_threshold_days:
            urgency = UrgencyLevel.CRITICAL
        elif weighted_days >= self.config.high_urgency_threshold_days:
            urgency = UrgencyLevel.HIGH
        elif weighted_days >= 14:  # 2 weeks overdue with adjustments
            urgency = UrgencyLevel.MEDIUM
        else:
            urgency = UrgencyLevel.LOW
        
        # Optional: Enhance with LLM analysis of clinical notes
        if clinical_notes:
            urgency = self._analyze_clinical_notes(urgency, clinical_notes)
        
        return urgency

    def _analyze_clinical_notes(
        self, base_urgency: UrgencyLevel, notes: str
    ) -> UrgencyLevel:
        """
        Enhance urgency scoring with AI analysis of clinical notes.
        
        This is a placeholder for future LLM integration that could identify
        risk factors in clinical notes (e.g., "patient reported pain",
        "swelling observed", "infection risk").
        
        Args:
            base_urgency: Urgency level from rule-based scoring
            notes: Clinical notes text
            
        Returns:
            Potentially adjusted urgency level
        """
        # Risk keywords that might indicate higher urgency
        high_risk_keywords = [
            "pain", "swelling", "infection", "bleeding", "discomfort",
            "abscess", "fracture", "emergency", "urgent"
        ]
        
        notes_lower = notes.lower()
        if any(keyword in notes_lower for keyword in high_risk_keywords):
            # Escalate urgency by one level if not already critical
            if base_urgency == UrgencyLevel.LOW:
                return UrgencyLevel.MEDIUM
            elif base_urgency == UrgencyLevel.MEDIUM:
                return UrgencyLevel.HIGH
            elif base_urgency == UrgencyLevel.HIGH:
                return UrgencyLevel.CRITICAL
        
        return base_urgency

    def sort_by_urgency(self, cases: list[FollowUpCase]) -> list[FollowUpCase]:
        """
        Sort follow-up cases by urgency level and days overdue.
        
        This creates a prioritized work queue for the agent, ensuring
        critical cases are handled first.
        
        Args:
            cases: List of follow-up cases to sort
            
        Returns:
            Sorted list with most urgent cases first
        """
        # Define urgency priority order
        urgency_priority = {
            UrgencyLevel.CRITICAL: 4,
            UrgencyLevel.HIGH: 3,
            UrgencyLevel.MEDIUM: 2,
            UrgencyLevel.LOW: 1
        }
        
        # Sort by urgency (descending) then by days overdue (descending)
        return sorted(
            cases,
            key=lambda c: (urgency_priority[c.urgency], c.days_overdue),
            reverse=True
        )
