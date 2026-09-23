# Quick Start Guide

Get up and running with the Patient Follow-up Agent in 5 minutes.

## Installation

```bash
# Navigate to project directory
cd agent_followship

# Install dependencies
pip install -r requirements.txt
```

## Option 1: Interactive Demo (Recommended for First Time)

```bash
python demo.py
```

This will:
- Initialize sample data with 12 diverse patients
- Walk you through all agent capabilities
- Demonstrate the agentic loop in action

**Estimated time: 5-10 minutes**

## Option 2: Web Dashboard

```bash
python app.py
```

Then open your browser to: **http://localhost:8080**

Features:
- 📊 Real-time statistics
- 📋 Active case monitoring
- ⚠️ Escalation tracking
- 🔄 Manual cycle triggering
- 📝 Audit log viewer

## Option 3: Direct Python Usage

```python
from orchestrator import FollowUpAgentOrchestrator
from data_access import MockPatientDataStore, MockCalendarIntegration
from sample_data import initialize_sample_data

# Setup
data_store = MockPatientDataStore()
calendar = MockCalendarIntegration()
initialize_sample_data(data_store, calendar)

# Create agent
agent = FollowUpAgentOrchestrator(data_store, calendar)

# Run daily cycle
cases = agent.run_daily_cycle()
print(f"Processed {len(cases)} cases")

# Simulate patient reply
agent.handle_incoming_reply("P001", "Yes, I'd like to book")

# View statistics
stats = agent.get_statistics()
print(stats)
```

## Option 4: LLM Tool Layer (Message Sending)

Send WhatsApp/SMS/email messages through LLM tool calling. No credentials and
no network access are needed: the demonstration and tests are dry-run by
default and use injected fake transports.

```bash
# Offline demonstration of all three scenarios
.venv/bin/python -m tools.demo_tool_use

# Run the tool-layer test suite
.venv/bin/python -m pytest tests/ -q
```

Drive it from Python:

```python
from tools import build_tool_registry, get_tool_schemas

registry = build_tool_registry()   # reads configuration from the environment

result = registry.call(
    "send_whatsapp_message",
    {
        "recipient": "+6591234567",
        "template": "appointment_reminder",
        "params": ["Sarah Johnson", "2026-04-12", "10:00 AM"],
    },
)

print(result.status)                      # 'sent' | 'failed' | 'ok' | 'escalated'
print(result.error_code)                  # e.g. 'recipient_not_verified'
print(result.suggested_fallback_channels) # e.g. ['sms', 'email']

tools = get_tool_schemas()  # pass to the Claude API as tools=[...]
```

The tools never raise: provider failures come back as a structured result, so
the model can retry on another channel, email the patient, or escalate to staff.

### Real AWS delivery: `send_sms` and `send_email`

Two additional tools transmit over the clinic's AWS account -- SMS via AWS End
User Messaging (`pinpoint-sms-voice-v2`) and email via Amazon SES. Both take a
`reason` that the agent must supply; it is printed as `[Agent决策] ...` and kept
in the audit trail.

```python
registry.call("send_sms", {
    "phone_number": "+6583536885",   # must be a verified destination
    "message": "Time for your 6-month check-up.",
    "reason": "patient is 7 months past their last visit",
})

registry.call("send_email", {
    "to_email": "martinchenonly1@gmail.com",   # must be SES-verified
    "subject": "Time for your check-up",
    "body": "Our records show it has been a while since your last visit.",
    "reason": "no reply to the SMS reminder",
})
```

Setup:

```bash
.venv/bin/pip install boto3            # required by the two AWS tools only
export AWS_REGION=ap-southeast-1
export AWS_SES_SOURCE=martinchenonly1@gmail.com
export AWS_SES_CONFIGURATION_SET=patient-followup   # makes delivery verifiable
export AWS_SMS_ALLOWED_NUMBERS=+6583536885
export AWS_EMAIL_ALLOWED_ADDRESSES=martinchenonly1@gmail.com
```

Credentials come from the standard boto3 chain, with one caveat: a CLI
`login_session` (`aws login`) is invisible to botocore, so export it first --
otherwise the tools report `config_missing` with `NoCredentialsError`.

```bash
eval "$(aws configure export-credentials --export-env)"
```

While the account is sandboxed the tools **refuse** any recipient outside those
allow-lists (`error_code: 'recipient_not_verified'`, `provider_code:
'allowlist'`) before calling AWS -- the model sees a normal failure and picks
another channel. SMS additionally needs the account onboarded to AWS End User
Messaging; until then a real attempt returns `error_code: 'not_subscribed'`,
which is non-retryable and points the agent at `escalate_to_staff`.

To send for real:

```bash
cp .env.example .env    # fill in credentials, then set MESSAGING_DRY_RUN=0
set -a; . ./.env; set +a
```

Nothing is transmitted while `MESSAGING_DRY_RUN` is unset, even with
credentials present.

For live Claude tool use: `pip install anthropic`, set `ANTHROPIC_API_KEY`, and
use `tools.llm_agent.ToolUseAgent` with `create_anthropic_client()`.

## Key Concepts

### The Agentic Loop

```
PERCEIVE → DECIDE → ACT → OBSERVE → (repeat)
```

1. **PERCEIVE**: Identify overdue patients from data store
2. **DECIDE**: Score urgency and prioritize cases
3. **ACT**: Send reminders, book appointments, or escalate
4. **OBSERVE**: Process patient replies and update state

### Urgency Levels

- 🔴 **CRITICAL**: Immediate staff attention required (e.g., post-surgery 60+ days overdue)
- 🟠 **HIGH**: Urgent follow-up needed (e.g., treatment interrupted)
- 🟡 **MEDIUM**: Should schedule soon (e.g., 2-4 weeks overdue)
- 🟢 **LOW**: Routine reminder (e.g., just past due date)

### Case Statuses

- **PENDING**: Identified, not yet contacted
- **MESSAGE_SENT**: Reminder sent to patient
- **AWAITING_REPLY**: Waiting for patient response
- **BOOKED**: Appointment successfully scheduled
- **DECLINED**: Patient declined follow-up
- **ESCALATED**: Transferred to staff for manual handling

## Testing Different Scenarios

The system includes diverse patient profiles:

```bash
# Critical case: Sarah Johnson (P001)
# Post-surgery follow-up 69 days overdue

# Patient with questions: David Kim (P004)
# Will trigger escalation when asking complex questions

# Reliable patient: Robert Anderson (P006)
# Good candidate for testing successful booking flow

# No-show history: Emily Rodriguez (P003)
# Demonstrates priority escalation for unreliable patients
```

## Common Tasks

### View All Cases

```python
cases = agent.get_active_cases()
for case in cases:
    print(f"{case.patient.name}: {case.urgency.value} - {case.status.value}")
```

### Check Escalations

```python
escalations = agent.escalation_handler.get_escalated_cases()
print(f"Cases needing staff attention: {len(escalations)}")
```

### View Audit Logs

```python
logs = agent.audit_logger.get_patient_history("P001")
for log in logs:
    print(f"{log['timestamp']}: {log['event_type']}")
```

## API Endpoints (Web Mode)

```bash
# Get status
curl http://localhost:8080/api/status

# Get all cases
curl http://localhost:8080/api/cases

# Run daily cycle
curl -X POST http://localhost:8080/api/run-cycle

# Simulate patient reply
curl -X POST http://localhost:8080/api/simulate-reply \
  -H "Content-Type: application/json" \
  -d '{"patient_id": "P001", "message": "Yes, please book me"}'
```

## Troubleshooting

### Port 5000 already in use

```bash
# Use a different port
python app.py --port 8080
```

Or modify `app.py`:
```python
app.run(debug=True, host='0.0.0.0', port=8080)
```

### Import errors

```bash
# Make sure you're in the project directory
cd agent_followship

# Reinstall dependencies
pip install -r requirements.txt
```

### No sample data

The sample data is automatically initialized when running:
- `python demo.py`
- `python app.py`

For manual initialization:
```python
from sample_data import initialize_sample_data
initialize_sample_data(data_store, calendar)
```

## Next Steps

1. ✅ Run the demo to understand capabilities
2. ✅ Explore the web dashboard
3. ✅ Review the code documentation
4. ✅ Examine sample data scenarios
5. ✅ Check the comprehensive README.md

## Need Help?

- 📖 Full documentation: `README.md`
- 🎯 Sample scenarios: `sample_data.py`
- 🔍 Code examples: `demo.py`
- 🌐 Web interface: `http://localhost:8080`

---

**Ready to go!** Start with `python demo.py` 🚀
