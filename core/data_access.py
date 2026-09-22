"""
Data access layer for the Patient Follow-up Agent.

This module provides abstract interfaces for data storage and calendar integration,
along with concrete mock implementations for demonstration purposes.
In production, these would connect to actual clinic systems (PMS/EHR, scheduling software).
"""

from abc import ABC, abstractmethod
from datetime import date, timedelta
from typing import Optional
import json
from pathlib import Path

from core.models import PatientRecord, ContactChannel


class PatientDataStore(ABC):
    """
    Abstract interface for patient data persistence.
    
    This interface abstracts away the underlying storage mechanism,
    allowing the agent to work with different clinic systems
    (SQL databases, REST APIs, file-based systems, etc.).
    """

    @abstractmethod
    def get_all_active_patients(self) -> list[PatientRecord]:
        """
        Retrieve all active patients who are eligible for follow-up.
        
        Returns:
            List of active patient records
        """
        pass

    @abstractmethod
    def get_patient_by_id(self, patient_id: str) -> Optional[PatientRecord]:
        """
        Retrieve a specific patient by their unique identifier.
        
        Args:
            patient_id: Unique patient identifier
            
        Returns:
            PatientRecord if found, None otherwise
        """
        pass

    @abstractmethod
    def update_last_contacted(self, patient_id: str, when: date) -> None:
        """
        Update the last contact date for a patient.
        
        Args:
            patient_id: Unique patient identifier
            when: Date when patient was last contacted
        """
        pass


class MockPatientDataStore(PatientDataStore):
    """
    Mock implementation of patient data storage using in-memory storage.
    
    This implementation stores patient records in memory and provides
    sample data for demonstration purposes. In production, this would
    be replaced with actual database connectivity.
    """

    def __init__(self):
        """Initialize with empty patient storage."""
        self._patients: dict[str, PatientRecord] = {}
        self._last_contacted: dict[str, date] = {}

    def add_patient(self, patient: PatientRecord) -> None:
        """
        Add a patient to the mock data store.
        
        Args:
            patient: Patient record to add
        """
        self._patients[patient.patient_id] = patient

    def get_all_active_patients(self) -> list[PatientRecord]:
        """Return all stored patients."""
        return list(self._patients.values())

    def get_patient_by_id(self, patient_id: str) -> Optional[PatientRecord]:
        """Retrieve patient by ID."""
        return self._patients.get(patient_id)

    def update_last_contacted(self, patient_id: str, when: date) -> None:
        """Record when a patient was last contacted."""
        self._last_contacted[patient_id] = when

    def get_last_contacted(self, patient_id: str) -> Optional[date]:
        """
        Retrieve the last contact date for a patient.
        
        Args:
            patient_id: Unique patient identifier
            
        Returns:
            Date of last contact, or None if never contacted
        """
        return self._last_contacted.get(patient_id)


class CalendarIntegration(ABC):
    """
    Abstract interface for appointment scheduling system integration.
    
    This interface allows the agent to interact with the clinic's
    scheduling system to find available slots and book appointments.
    """

    @abstractmethod
    def find_available_slots(
        self, treatment_type: str, after: date, limit: int = 5
    ) -> list[date]:
        """
        Find available appointment slots for a given treatment type.
        
        Args:
            treatment_type: Type of treatment requiring appointment
            after: Find slots after this date
            limit: Maximum number of slots to return
            
        Returns:
            List of available dates
        """
        pass

    @abstractmethod
    def book_appointment(
        self, patient_id: str, appointment_date: date, treatment_type: str
    ) -> bool:
        """
        Book an appointment for a patient.
        
        Args:
            patient_id: Unique patient identifier
            appointment_date: Requested appointment date
            treatment_type: Type of treatment
            
        Returns:
            True if booking successful, False otherwise
        """
        pass

    @abstractmethod
    def cancel_appointment(self, patient_id: str, appointment_date: date) -> bool:
        """
        Cancel an existing appointment.
        
        Args:
            patient_id: Unique patient identifier
            appointment_date: Date of appointment to cancel
            
        Returns:
            True if cancellation successful, False otherwise
        """
        pass


class MockCalendarIntegration(CalendarIntegration):
    """
    Mock implementation of calendar/scheduling system.
    
    Simulates appointment availability and booking for demonstration.
    In production, this would integrate with actual scheduling software
    (e.g., Dentrix, Open Dental, Google Calendar API).
    """

    def __init__(self):
        """Initialize with empty appointment storage."""
        # Dictionary mapping (patient_id, date) to treatment_type
        self._appointments: dict[tuple[str, date], str] = {}
        # Simulated blocked dates (weekends, holidays, etc.)
        self._blocked_dates: set[date] = set()

    def find_available_slots(
        self, treatment_type: str, after: date, limit: int = 5
    ) -> list[date]:
        """
        Generate mock available slots.
        
        Simulates availability by returning weekday dates that aren't
        already fully booked.
        """
        available_slots = []
        current_date = after + timedelta(days=1)
        
        while len(available_slots) < limit:
            # Skip weekends (5=Saturday, 6=Sunday)
            if current_date.weekday() < 5 and current_date not in self._blocked_dates:
                # Check if date has capacity (simplified: max 10 appointments per day)
                daily_appointments = sum(
                    1 for (_, appt_date) in self._appointments.keys()
                    if appt_date == current_date
                )
                if daily_appointments < 10:
                    available_slots.append(current_date)
            
            current_date += timedelta(days=1)
            
            # Safety: don't search more than 60 days ahead
            if current_date > after + timedelta(days=60):
                break
        
        return available_slots

    def book_appointment(
        self, patient_id: str, appointment_date: date, treatment_type: str
    ) -> bool:
        """
        Book a mock appointment.
        
        Checks for conflicts and availability before booking.
        """
        # Check if slot is available
        key = (patient_id, appointment_date)
        
        # Check if patient already has appointment on this date
        if key in self._appointments:
            return False
        
        # Check if date is blocked
        if appointment_date in self._blocked_dates:
            return False
        
        # Check weekday
        if appointment_date.weekday() >= 5:
            return False
        
        # Book the appointment
        self._appointments[key] = treatment_type
        return True

    def cancel_appointment(self, patient_id: str, appointment_date: date) -> bool:
        """Cancel a mock appointment."""
        key = (patient_id, appointment_date)
        if key in self._appointments:
            del self._appointments[key]
            return True
        return False

    def get_appointments_for_patient(self, patient_id: str) -> list[tuple[date, str]]:
        """
        Get all appointments for a specific patient.
        
        Args:
            patient_id: Unique patient identifier
            
        Returns:
            List of (date, treatment_type) tuples
        """
        return [
            (appt_date, treatment)
            for (pid, appt_date), treatment in self._appointments.items()
            if pid == patient_id
        ]

    def block_date(self, block_date: date) -> None:
        """
        Mark a date as unavailable (e.g., holiday, clinic closed).
        
        Args:
            block_date: Date to block
        """
        self._blocked_dates.add(block_date)
