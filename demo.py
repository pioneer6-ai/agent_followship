"""
Demo script for the Patient Follow-up Agent.

This script provides an interactive demonstration of the agent's capabilities,
showing the complete agentic loop in action with realistic scenarios.
"""

from datetime import date
from agent.orchestrator import FollowUpAgentOrchestrator
from core.data_access import MockPatientDataStore, MockCalendarIntegration
from core.config import ClinicPolicyConfig
from utils.sample_data import initialize_sample_data, print_sample_data_summary


def print_banner(text: str) -> None:
    """Print a formatted banner."""
    print("\n" + "="*70)
    print(f"  {text}")
    print("="*70 + "\n")


def demo_daily_cycle(agent: FollowUpAgentOrchestrator) -> None:
    """
    Demonstrate a complete daily cycle of the agent.
    
    Shows the Perceive -> Decide -> Act flow.
    """
    print_banner("DEMO: Daily Agent Cycle (Perceive -> Decide -> Act)")
    
    print("This demonstrates the agent's autonomous daily workflow:")
    print("1. PERCEIVE: Gather data about overdue patients")
    print("2. DECIDE: Evaluate urgency and prioritize cases")
    print("3. ACT: Send reminders and take appropriate actions\n")
    
    input("Press Enter to run the daily cycle...")
    
    agent.run_daily_cycle(date.today())
    
    input("\nPress Enter to continue...")


def demo_patient_interaction(agent: FollowUpAgentOrchestrator) -> None:
    """
    Demonstrate the agent handling patient replies.
    
    Shows the Observe -> Decide -> Act flow for interactive conversations.
    """
    print_banner("DEMO: Patient Interaction (Observe -> Decide -> Act)")
    
    print("This demonstrates the agent's conversational capabilities:")
    print("- Intent recognition from patient messages")
    print("- Autonomous decision-making based on context")
    print("- Appropriate action execution\n")
    
    # Scenario 1: Patient confirms booking
    print("📧 Scenario 1: Patient Confirms Appointment")
    print("-" * 70)
    patient_id = "P006"  # Robert Anderson - low urgency cleaning
    case = agent.get_case_by_patient_id(patient_id)
    if case:
        print(f"Patient: {case.patient.name}")
        print(f"Status before: {case.status.value}\n")
        
        message = "Yes, I'd like to schedule an appointment"
        print(f'Patient sends: "{message}"')
        
        agent.handle_incoming_reply(patient_id, message)
        
        updated_case = agent.get_case_by_patient_id(patient_id)
        print(f"Status after: {updated_case.status.value}")
    
    input("\nPress Enter for next scenario...")
    
    # Scenario 2: Patient declines
    print("\n📧 Scenario 2: Patient Declines Follow-up")
    print("-" * 70)
    patient_id = "P007"  # Lisa Martinez - checkup
    case = agent.get_case_by_patient_id(patient_id)
    if case:
        print(f"Patient: {case.patient.name}")
        print(f"Status before: {case.status.value}\n")
        
        message = "No thanks, not needed right now"
        print(f'Patient sends: "{message}"')
        
        agent.handle_incoming_reply(patient_id, message)
        
        updated_case = agent.get_case_by_patient_id(patient_id)
        print(f"Status after: {updated_case.status.value}")
    
    input("\nPress Enter for next scenario...")
    
    # Scenario 3: Patient asks question (triggers escalation)
    print("\n📧 Scenario 3: Patient Asks Complex Question")
    print("-" * 70)
    patient_id = "P004"  # David Kim - orthodontic
    case = agent.get_case_by_patient_id(patient_id)
    if case:
        print(f"Patient: {case.patient.name}")
        print(f"Status before: {case.status.value}\n")
        
        message = "How much will this cost with my insurance?"
        print(f'Patient sends: "{message}"')
        print("(This should trigger escalation - agent knows its boundaries)\n")
        
        agent.handle_incoming_reply(patient_id, message)
        
        updated_case = agent.get_case_by_patient_id(patient_id)
        print(f"Status after: {updated_case.status.value}")
    
    input("\nPress Enter to continue...")


def demo_urgency_scoring(agent: FollowUpAgentOrchestrator) -> None:
    """
    Demonstrate the urgency scoring algorithm.
    
    Shows how the agent prioritizes cases based on multiple factors.
    """
    print_banner("DEMO: Intelligent Urgency Scoring")
    
    print("The agent evaluates multiple factors to prioritize cases:")
    print("- Days overdue")
    print("- Treatment type (some are more time-sensitive)")
    print("- Patient history (no-shows get earlier intervention)")
    print("- Clinical notes (future: AI analysis)\n")
    
    cases = agent.get_active_cases()
    
    # Sort by urgency for display
    urgency_order = {"critical": 4, "high": 3, "medium": 2, "low": 1}
    sorted_cases = sorted(
        cases,
        key=lambda c: (urgency_order.get(c.urgency.value, 0), c.days_overdue),
        reverse=True
    )
    
    print("📊 Prioritized Cases:\n")
    print(f"{'Patient':<20} {'Treatment':<25} {'Days Overdue':<15} {'Urgency':<10}")
    print("-" * 70)
    
    for case in sorted_cases[:10]:  # Show top 10
        treatment = case.patient.treatment_type.replace('_', ' ').title()
        urgency_emoji = {
            "critical": "🔴",
            "high": "🟠", 
            "medium": "🟡",
            "low": "🟢"
        }.get(case.urgency.value, "⚪")
        
        print(f"{case.patient.name:<20} {treatment:<25} {case.days_overdue:<15} "
              f"{urgency_emoji} {case.urgency.value.upper()}")
    
    input("\nPress Enter to continue...")


def demo_escalation(agent: FollowUpAgentOrchestrator) -> None:
    """
    Demonstrate the escalation mechanism.
    
    Shows how the agent knows when to transfer cases to human staff.
    """
    print_banner("DEMO: Escalation to Human Staff")
    
    print("The agent demonstrates self-awareness by escalating when:")
    print("- Too many unanswered reminders")
    print("- Critical urgency with no response")
    print("- Patient asks complex questions")
    print("- Patient seems confused or frustrated\n")
    
    escalations = agent.escalation_handler.get_escalated_cases()
    
    if escalations:
        print(f"⚠️  {len(escalations)} case(s) escalated to staff:\n")
        
        for escalation in escalations:
            print(f"Patient: {escalation['patient_name']}")
            print(f"Priority: {escalation['priority'].upper()}")
            print(f"Reason: {escalation['reason']}")
            print(f"Urgency: {escalation['urgency']}")
            print(f"Days Overdue: {escalation['days_overdue']}")
            print("-" * 70)
    else:
        print("ℹ️  No escalations yet (will appear after running daily cycle)")
    
    input("\nPress Enter to continue...")


def demo_audit_trail(agent: FollowUpAgentOrchestrator) -> None:
    """
    Demonstrate the audit logging system.
    
    Shows transparency and accountability features.
    """
    print_banner("DEMO: Audit Trail & Compliance")
    
    print("Every agent decision and action is logged for:")
    print("- Regulatory compliance (HIPAA, PDPA, GDPR)")
    print("- Quality assurance")
    print("- System improvement")
    print("- Liability protection\n")
    
    summary = agent.audit_logger.generate_summary_report()
    
    print("📋 Audit Summary:")
    print(f"   Total Decisions: {summary['total_decisions']}")
    print(f"   Total Communications: {summary['total_communications']}")
    print(f"   Successful Bookings: {summary['successful_bookings']}")
    print(f"   Total Log Entries: {summary['total_log_entries']}\n")
    
    # Show recent log entries
    recent_logs = sorted(
        agent.audit_logger.log_entries,
        key=lambda x: x['timestamp'],
        reverse=True
    )[:5]
    
    if recent_logs:
        print("📝 Recent Log Entries:\n")
        for log in recent_logs:
            print(f"[{log['timestamp']}] {log['event_type']}")
            if 'rationale' in log:
                print(f"   {log['rationale'][:80]}...")
            print()
    
    input("\nPress Enter to continue...")


def demo_statistics(agent: FollowUpAgentOrchestrator) -> None:
    """Display agent operational statistics."""
    print_banner("DEMO: Operational Statistics")
    
    stats = agent.get_statistics()
    
    print(f"📊 Active Cases: {stats['total_active_cases']}\n")
    
    print("By Status:")
    for status, count in stats['cases_by_status'].items():
        if count > 0:
            print(f"   {status.replace('_', ' ').title()}: {count}")
    
    print("\nBy Urgency:")
    for urgency, count in stats['cases_by_urgency'].items():
        if count > 0:
            urgency_emoji = {
                "critical": "🔴",
                "high": "🟠",
                "medium": "🟡",
                "low": "🟢"
            }.get(urgency, "⚪")
            print(f"   {urgency_emoji} {urgency.title()}: {count}")
    
    print(f"\n⚠️  Escalated Cases: {stats['escalated_cases']}")
    
    input("\nPress Enter to continue...")


def main():
    """Run the interactive demo."""
    print("\n" + "="*70)
    print("  🦷 PATIENT FOLLOW-UP AGENT - INTERACTIVE DEMO")
    print("="*70)
    print("\nWelcome to the Patient Follow-up Agent demonstration!")
    print("This demo showcases an autonomous AI agent for dental clinic")
    print("patient recall management.\n")
    
    input("Press Enter to start...")
    
    # Initialize system
    print("\n🔧 Initializing system...")
    data_store = MockPatientDataStore()
    calendar = MockCalendarIntegration()
    policy = ClinicPolicyConfig()
    
    initialize_sample_data(data_store, calendar)
    print_sample_data_summary(data_store)
    
    agent = FollowUpAgentOrchestrator(data_store, calendar, policy)
    
    # Run demos
    demos = [
        ("Daily Agent Cycle", demo_daily_cycle),
        ("Patient Interactions", demo_patient_interaction),
        ("Urgency Scoring", demo_urgency_scoring),
        ("Escalation Mechanism", demo_escalation),
        ("Audit Trail", demo_audit_trail),
        ("Statistics Dashboard", demo_statistics),
    ]
    
    for title, demo_func in demos:
        demo_func(agent)
    
    # Final summary
    print_banner("DEMO COMPLETE")
    
    print("✅ Demonstration completed successfully!\n")
    print("Key Takeaways:")
    print("1. 🤖 Autonomous operation with Perceive->Decide->Act->Observe loop")
    print("2. 🧠 Intelligent decision-making based on multiple factors")
    print("3. 💬 Natural conversation handling with intent recognition")
    print("4. ⚠️  Self-aware escalation when human expertise needed")
    print("5. 📝 Complete audit trail for compliance and accountability")
    print("6. 🎯 Prioritization based on clinical urgency and patient history")
    print("\n🌐 Web Dashboard: Run 'python web/app.py' to access the visual interface")
    print("="*70 + "\n")


if __name__ == "__main__":
    main()
