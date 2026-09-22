"""
Sample data generator for the Patient Follow-up Agent.

This module creates realistic test data including:
- Diverse patient profiles with various treatment types
- Different overdue scenarios (low to critical urgency)
- Mixed communication preferences
- Varied patient histories (no-shows, compliance patterns)

The sample data is designed to showcase all agent capabilities
during demonstrations and testing.
"""

from datetime import date, timedelta
from core.models import PatientRecord, ContactChannel
from core.data_access import MockPatientDataStore, MockCalendarIntegration


def initialize_sample_data(
    data_store: MockPatientDataStore, 
    calendar: MockCalendarIntegration
) -> None:
    """
    Initialize the system with comprehensive sample data.
    
    Creates a diverse set of patient records representing different
    scenarios the agent needs to handle, from routine follow-ups
    to critical overdue cases.
    
    Args:
        data_store: Patient data store to populate
        calendar: Calendar integration to configure
    """
    today = date.today()
    
    # Sample patients with varying scenarios
    sample_patients = [
        # 1. Critical case: Post-surgery follow-up severely overdue
        PatientRecord(
            patient_id="P001",
            name="Sarah Johnson",
            contact_info={
                ContactChannel.SMS: "+1-555-0101",
                ContactChannel.EMAIL: "sarah.j@email.com",
                ContactChannel.WHATSAPP: "+1-555-0101"
            },
            preferred_channel=ContactChannel.SMS,
            last_visit_date=today - timedelta(days=90),  # 90 days ago
            treatment_type="post_surgery",
            recall_interval_days=21,  # Should have returned after 3 weeks
            no_show_history=0,
            language="en"
        ),
        
        # 2. High urgency: Root canal follow-up overdue
        PatientRecord(
            patient_id="P002",
            name="Michael Chen",
            contact_info={
                ContactChannel.WHATSAPP: "+1-555-0102",
                ContactChannel.EMAIL: "mchen@email.com"
            },
            preferred_channel=ContactChannel.WHATSAPP,
            last_visit_date=today - timedelta(days=50),
            treatment_type="root_canal_followup",
            recall_interval_days=14,
            no_show_history=1,
            language="en"
        ),
        
        # 3. High urgency: Cavity treatment overdue, history of no-shows
        PatientRecord(
            patient_id="P003",
            name="Emily Rodriguez",
            contact_info={
                ContactChannel.SMS: "+1-555-0103",
                ContactChannel.EMAIL: "emily.r@email.com",
                ContactChannel.PHONE_CALL: "+1-555-0103"
            },
            preferred_channel=ContactChannel.SMS,
            last_visit_date=today - timedelta(days=55),
            treatment_type="cavity_treatment",
            recall_interval_days=30,
            no_show_history=3,  # Frequent no-shows - needs attention
            language="en"
        ),
        
        # 4. Medium urgency: Orthodontic adjustment overdue
        PatientRecord(
            patient_id="P004",
            name="David Kim",
            contact_info={
                ContactChannel.EMAIL: "david.kim@email.com",
                ContactChannel.SMS: "+1-555-0104"
            },
            preferred_channel=ContactChannel.EMAIL,
            last_visit_date=today - timedelta(days=38),
            treatment_type="orthodontic_adjustment",
            recall_interval_days=28,
            no_show_history=0,
            language="en"
        ),
        
        # 5. Medium urgency: Periodontal maintenance overdue
        PatientRecord(
            patient_id="P005",
            name="Jennifer Taylor",
            contact_info={
                ContactChannel.WHATSAPP: "+1-555-0105",
                ContactChannel.EMAIL: "j.taylor@email.com"
            },
            preferred_channel=ContactChannel.WHATSAPP,
            last_visit_date=today - timedelta(days=110),
            treatment_type="periodontal_maintenance",
            recall_interval_days=90,
            no_show_history=0,
            language="en"
        ),
        
        # 6. Low urgency: Routine cleaning slightly overdue
        PatientRecord(
            patient_id="P006",
            name="Robert Anderson",
            contact_info={
                ContactChannel.SMS: "+1-555-0106",
                ContactChannel.EMAIL: "r.anderson@email.com"
            },
            preferred_channel=ContactChannel.SMS,
            last_visit_date=today - timedelta(days=190),
            treatment_type="cleaning",
            recall_interval_days=180,
            no_show_history=0,
            language="en"
        ),
        
        # 7. Low urgency: General checkup overdue
        PatientRecord(
            patient_id="P007",
            name="Lisa Martinez",
            contact_info={
                ContactChannel.EMAIL: "lisa.m@email.com",
                ContactChannel.SMS: "+1-555-0107"
            },
            preferred_channel=ContactChannel.EMAIL,
            last_visit_date=today - timedelta(days=195),
            treatment_type="checkup",
            recall_interval_days=180,
            no_show_history=0,
            language="en"
        ),
        
        # 8. Medium urgency: Cleaning overdue, reliable patient
        PatientRecord(
            patient_id="P008",
            name="James Wilson",
            contact_info={
                ContactChannel.SMS: "+1-555-0108",
                ContactChannel.WHATSAPP: "+1-555-0108",
                ContactChannel.EMAIL: "james.w@email.com"
            },
            preferred_channel=ContactChannel.WHATSAPP,
            last_visit_date=today - timedelta(days=200),
            treatment_type="cleaning",
            recall_interval_days=180,
            no_show_history=0,
            language="en"
        ),
        
        # 9. High urgency: Post-surgery follow-up overdue
        PatientRecord(
            patient_id="P009",
            name="Maria Garcia",
            contact_info={
                ContactChannel.PHONE_CALL: "+1-555-0109",
                ContactChannel.SMS: "+1-555-0109"
            },
            preferred_channel=ContactChannel.PHONE_CALL,
            last_visit_date=today - timedelta(days=45),
            treatment_type="post_surgery",
            recall_interval_days=21,
            no_show_history=0,
            language="en"
        ),
        
        # 10. Low urgency: Orthodontic checkup slightly overdue
        PatientRecord(
            patient_id="P010",
            name="Kevin Brown",
            contact_info={
                ContactChannel.EMAIL: "kevin.b@email.com",
                ContactChannel.SMS: "+1-555-0110"
            },
            preferred_channel=ContactChannel.EMAIL,
            last_visit_date=today - timedelta(days=35),
            treatment_type="orthodontic_adjustment",
            recall_interval_days=28,
            no_show_history=0,
            language="en"
        ),
        
        # 11. Not overdue yet: Recent patient (for testing)
        PatientRecord(
            patient_id="P011",
            name="Amanda White",
            contact_info={
                ContactChannel.SMS: "+1-555-0111",
                ContactChannel.EMAIL: "amanda.w@email.com"
            },
            preferred_channel=ContactChannel.SMS,
            last_visit_date=today - timedelta(days=10),
            treatment_type="cleaning",
            recall_interval_days=180,
            no_show_history=0,
            language="en"
        ),
        
        # 12. Critical: Periodontal maintenance severely overdue with no-shows
        PatientRecord(
            patient_id="P012",
            name="Thomas Lee",
            contact_info={
                ContactChannel.SMS: "+1-555-0112",
                ContactChannel.PHONE_CALL: "+1-555-0112",
                ContactChannel.EMAIL: "thomas.lee@email.com"
            },
            preferred_channel=ContactChannel.SMS,
            last_visit_date=today - timedelta(days=150),
            treatment_type="periodontal_maintenance",
            recall_interval_days=90,
            no_show_history=2,
            language="en"
        ),
    ]
    
    # Add all patients to the data store
    for patient in sample_patients:
        data_store.add_patient(patient)
    
    # Block some dates in the calendar (weekends, holidays)
    # This creates realistic scheduling constraints
    current_date = today
    for i in range(60):  # Next 60 days
        check_date = current_date + timedelta(days=i)
        # Block Sundays (weekday 6)
        if check_date.weekday() == 6:
            calendar.block_date(check_date)
    
    print("✅ Sample data initialized successfully")
    print(f"   - {len(sample_patients)} patients added")
    print(f"   - Various urgency levels represented")
    print(f"   - Multiple treatment types included")
    print(f"   - Diverse communication preferences")


def create_test_scenarios() -> list[dict]:
    """
    Define test scenarios for demonstrating agent capabilities.
    
    These scenarios can be used for:
    - Automated testing
    - Live demonstrations
    - System validation
    - Training and documentation
    
    Returns:
        List of test scenario definitions
    """
    scenarios = [
        {
            "name": "Critical Escalation",
            "description": "Test agent's ability to recognize and escalate critical cases",
            "patient_id": "P001",
            "expected_behavior": "Agent should send urgent reminder and escalate to staff due to post-surgery overdue status",
            "test_actions": [
                {"action": "run_daily_cycle", "expected": "message_sent"},
                {"action": "wait_no_response", "expected": "escalated"}
            ]
        },
        {
            "name": "Successful Booking",
            "description": "Test complete booking flow from reminder to confirmation",
            "patient_id": "P006",
            "expected_behavior": "Agent sends reminder, patient confirms, appointment booked",
            "test_actions": [
                {"action": "run_daily_cycle", "expected": "message_sent"},
                {"action": "patient_reply", "message": "Yes, I'd like to book", "expected": "booked"}
            ]
        },
        {
            "name": "Patient Decline",
            "description": "Test agent's handling of patient declining follow-up",
            "patient_id": "P007",
            "expected_behavior": "Agent recognizes decline intent and marks case appropriately",
            "test_actions": [
                {"action": "run_daily_cycle", "expected": "message_sent"},
                {"action": "patient_reply", "message": "Not needed right now", "expected": "declined"}
            ]
        },
        {
            "name": "Reschedule Request",
            "description": "Test agent's ability to handle rescheduling requests",
            "patient_id": "P008",
            "expected_behavior": "Agent proposes alternative slots when patient requests different time",
            "test_actions": [
                {"action": "run_daily_cycle", "expected": "message_sent"},
                {"action": "patient_reply", "message": "Can I get a different time?", "expected": "propose_slot"}
            ]
        },
        {
            "name": "Question Escalation",
            "description": "Test agent's boundary awareness - escalate complex questions",
            "patient_id": "P004",
            "expected_behavior": "Agent recognizes question needs human expertise and escalates",
            "test_actions": [
                {"action": "run_daily_cycle", "expected": "message_sent"},
                {"action": "patient_reply", "message": "How much will this cost with my insurance?", "expected": "escalated"}
            ]
        },
        {
            "name": "No-Show Pattern",
            "description": "Test urgency scoring adjustment for patients with no-show history",
            "patient_id": "P003",
            "expected_behavior": "Agent should prioritize higher due to no-show history",
            "test_actions": [
                {"action": "run_daily_cycle", "check": "urgency_elevated"}
            ]
        },
        {
            "name": "Multi-Channel Communication",
            "description": "Test agent's respect for patient channel preferences",
            "patient_id": "P002",
            "expected_behavior": "Agent should use WhatsApp as preferred channel",
            "test_actions": [
                {"action": "run_daily_cycle", "check": "channel_whatsapp"}
            ]
        },
        {
            "name": "Rate Limiting",
            "description": "Test agent respects reminder interval policy",
            "patient_id": "P010",
            "expected_behavior": "Agent should not send reminders too frequently",
            "test_actions": [
                {"action": "run_daily_cycle", "expected": "message_sent"},
                {"action": "run_cycle_next_day", "expected": "do_nothing"},
                {"action": "run_cycle_7_days_later", "expected": "reminder_sent"}
            ]
        }
    ]
    
    return scenarios


def print_sample_data_summary(data_store: MockPatientDataStore) -> None:
    """
    Print a summary of the sample data for verification.
    
    Args:
        data_store: Patient data store to summarize
    """
    patients = data_store.get_all_active_patients()
    today = date.today()
    
    print("\n" + "="*70)
    print("📊 SAMPLE DATA SUMMARY")
    print("="*70)
    
    treatment_types = {}
    channels = {}
    overdue_count = 0
    
    for patient in patients:
        days_since_visit = (today - patient.last_visit_date).days
        days_overdue = days_since_visit - patient.recall_interval_days
        
        if days_overdue > 0:
            overdue_count += 1
        
        # Count treatment types
        treatment_types[patient.treatment_type] = \
            treatment_types.get(patient.treatment_type, 0) + 1
        
        # Count preferred channels
        channels[patient.preferred_channel.value] = \
            channels.get(patient.preferred_channel.value, 0) + 1
    
    print(f"\n📈 Statistics:")
    print(f"   Total Patients: {len(patients)}")
    print(f"   Overdue Patients: {overdue_count}")
    print(f"   On-Time Patients: {len(patients) - overdue_count}")
    
    print(f"\n🔬 Treatment Types:")
    for treatment, count in sorted(treatment_types.items()):
        print(f"   {treatment.replace('_', ' ').title()}: {count}")
    
    print(f"\n📱 Preferred Channels:")
    for channel, count in sorted(channels.items()):
        print(f"   {channel.upper()}: {count}")
    
    print("\n" + "="*70 + "\n")


if __name__ == "__main__":
    # For standalone testing
    data_store = MockPatientDataStore()
    calendar = MockCalendarIntegration()
    
    initialize_sample_data(data_store, calendar)
    print_sample_data_summary(data_store)
    
    print("\n📋 Test Scenarios Available:")
    scenarios = create_test_scenarios()
    for i, scenario in enumerate(scenarios, 1):
        print(f"\n{i}. {scenario['name']}")
        print(f"   Description: {scenario['description']}")
        print(f"   Patient: {scenario['patient_id']}")
        print(f"   Expected: {scenario['expected_behavior']}")
