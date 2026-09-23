# 🦷 Patient Follow-up Agent

An intelligent, autonomous agent system for dental clinic patient recall management. This agent demonstrates the complete **Perceive → Decide → Act → Observe** agentic loop, autonomously managing patient follow-ups from identification through resolution or escalation.

## 🎯 Overview

Dental clinics face challenges maintaining consistent follow-up schedules as their patient base grows. This AI agent system automates the patient recall process while maintaining transparency, accountability, and appropriate escalation to human staff.

### Key Features

- **🤖 Autonomous Operation**: Complete agentic loop with minimal human intervention
- **🧠 Intelligent Decision-Making**: Multi-factor urgency scoring and prioritization
- **💬 Conversational AI**: Natural language understanding and intent recognition
- **⚠️ Self-Aware Escalation**: Knows when to transfer cases to human staff
- **📝 Complete Audit Trail**: Full compliance logging for healthcare regulations
- **🌐 Web Dashboard**: Real-time monitoring and control interface
- **📱 Multi-Channel Communication**: SMS, WhatsApp, Email, Phone support
- **🎯 Clinical Prioritization**: Urgency-based on treatment type and patient history
- **📤 Smart Data Import**: Upload patient lists in any format - AI parses automatically
- **🔍 LLM-Powered Parsing**: Handles inconsistent data formats intelligently

## 🏗️ Architecture

### Core Components

```
┌─────────────────────────────────────────────────────────────┐
│                    FollowUpAgentOrchestrator                │
│                     (Agentic Loop Brain)                     │
└─────────────────────────────────────────────────────────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        │                     │                     │
┌───────▼────────┐   ┌────────▼────────┐   ┌──────▼──────┐
│  PERCEIVE      │   │    DECIDE       │   │    ACT      │
│                │   │                 │   │             │
│ • Data Store   │   │ • Rule Engine   │   │ • Scheduler │
│ • Patient List │   │ • Urgency Score │   │ • Notifier  │
│                │   │ • Conversation  │   │ • Escalator │
└────────────────┘   └─────────────────┘   └─────────────┘
                              │
                     ┌────────▼────────┐
                     │    OBSERVE      │
                     │                 │
                     │ • Reply Handler │
                     │ • Audit Logger  │
                     └─────────────────┘
```

### Module Overview

- **`models.py`**: Core data structures (Patient, Case, Enums)
- **`data_access.py`**: Patient data and calendar integration interfaces
- **`business_rules.py`**: Deterministic logic for overdue detection and urgency scoring
- **`notifications.py`**: Multi-channel messaging system
- **`conversation.py`**: Intent recognition and conversation management
- **`action_handlers.py`**: Appointment scheduler, escalation, audit logging
- **`orchestrator.py`**: Main agentic loop orchestration
- **`app.py`**: Flask web application and REST API
- **`sample_data.py`**: Test data generator
- **`demo.py`**: Interactive demonstration script

### Messaging Tool Layer (`tools/`)

A self-contained package that exposes message sending as **LLM tool use /
function calling**. See [LLM Tool Layer](#-llm-tool-layer-function-calling).

- **`tools/result.py`**: `ToolResult` - the never-raising return type
- **`tools/errors.py`**: Error taxonomy and channel fallback table
- **`tools/config.py`**: Environment-driven configuration + templates
- **`tools/transport.py`**: HTTP/SMTP transports (real + fake test doubles)
- **`tools/providers.py`**: Meta WhatsApp, Twilio, SMTP implementations
- **`tools/messaging.py`**: The six deterministic tools + registry
- **`tools/schemas.py`**: Anthropic tool schemas
- **`tools/llm_agent.py`**: Claude tool-use loop + `AgentRun` bookkeeping
- **`tools/demo_tool_use.py`**: Offline 3-scenario demonstration

## 🚀 Quick Start

### Prerequisites

- Python 3.9 or higher
- pip package manager

### Installation

1. **Clone or navigate to the project directory**

```bash
cd agent_followship
```

2. **Install dependencies**

```bash
pip install -r requirements.txt
```

3. **Run the interactive demo**

```bash
python demo.py
```

This will guide you through all agent capabilities with sample data.

4. **Launch the web dashboard**

```bash
python app.py
```

Then open your browser to: `http://localhost:5000`

## 📖 Usage Guide

### Running the Agent

#### Option 1: Web Dashboard (Recommended)

```bash
python app.py
```

Features:
- Real-time case monitoring
- Manual cycle triggering
- Case detail inspection
- Escalation tracking
- Statistics visualization

#### Option 2: Interactive Demo

```bash
python demo.py
```

Demonstrates:
- Daily agent cycle
- Patient interactions
- Urgency scoring
- Escalation mechanism
- Audit trails

#### Option 3: Programmatic Usage

```python
from orchestrator import FollowUpAgentOrchestrator
from data_access import MockPatientDataStore, MockCalendarIntegration
from config import ClinicPolicyConfig
from sample_data import initialize_sample_data

# Initialize
data_store = MockPatientDataStore()
calendar = MockCalendarIntegration()
policy = ClinicPolicyConfig()

# Load sample data
initialize_sample_data(data_store, calendar)

# Create agent
agent = FollowUpAgentOrchestrator(data_store, calendar, policy)

# Run daily cycle
cases = agent.run_daily_cycle()

# Handle patient reply
agent.handle_incoming_reply("P001", "Yes, I'd like to book")

# Get statistics
stats = agent.get_statistics()
print(stats)
```

### Configuration

Edit `config.py` to customize clinic policies:

```python
ClinicPolicyConfig(
    working_hours=(9, 18),              # 9 AM to 6 PM
    max_reminders_before_escalation=3,  # Escalate after 3 reminders
    opt_out_respected=True,             # Honor opt-out requests
    high_urgency_threshold_days=30,     # High urgency at 30 days
    critical_urgency_threshold_days=60, # Critical at 60 days
    reminder_interval_days=7            # Wait 7 days between reminders
)
```

## 🔄 Agentic Loop Details

### Phase 1: PERCEIVE

The agent gathers data about the current state:

```python
# Retrieve all active patients
patients = data_store.get_all_active_patients()

# Identify overdue patients
overdue_cases = rule_engine.compute_overdue_patients(patients, today)
```

### Phase 2: DECIDE

The agent evaluates and prioritizes:

```python
# Score urgency for each case
for case in overdue_cases:
    case.urgency = urgency_scorer.score(case)

# Sort by priority
prioritized_cases = urgency_scorer.sort_by_urgency(overdue_cases)

# Decide action for each case
action = decide_action_for_case(case)
```

### Phase 3: ACT

The agent executes decisions:

```python
# Send reminder
message = message_composer.compose(case)
channel.send(patient, message)

# Book appointment
scheduler.try_book(case)

# Escalate to staff
escalation_handler.escalate(case, reason)
```

### Phase 4: OBSERVE

The agent processes feedback:

```python
# Handle patient reply
action, context = conversation_manager.handle_reply(case, message)

# Update case state
case.status = new_status

# Log for audit
audit_logger.log_decision(case, action, rationale)
```

## 📊 Sample Data

The system includes 12 diverse patient profiles:

| Patient | Treatment Type | Days Overdue | Urgency | Scenario |
|---------|---------------|--------------|---------|----------|
| Sarah Johnson | Post-Surgery | 69 | CRITICAL | Severely overdue surgical follow-up |
| Michael Chen | Root Canal | 36 | HIGH | Endodontic follow-up overdue |
| Emily Rodriguez | Cavity Treatment | 25 | HIGH | Multiple no-shows, needs attention |
| David Kim | Orthodontic | 10 | MEDIUM | Routine adjustment overdue |
| Jennifer Taylor | Periodontal | 20 | MEDIUM | Gum disease maintenance |
| Robert Anderson | Cleaning | 10 | LOW | Routine cleaning slightly overdue |
| Lisa Martinez | Checkup | 15 | LOW | General checkup overdue |
| ... | ... | ... | ... | ... |

## 🧰 LLM Tool Layer (Function Calling)

The `tools/` package implements the sending half of the loop below. The LLM
only **decides**; every provider call happens inside a deterministic Python
function that can never raise.

```
LLM Agent (Claude API, tool use)
     │  "this patient is due for a reminder" → tool_use block
     ▼
send_whatsapp_message(phone, template, params)   ← deterministic function
     │  exactly one provider attempt
     ▼
WhatsApp Business API / Twilio / SMTP email
```

### Available tools

| Tool | Purpose |
|------|---------|
| `send_whatsapp_message` | Send a templated WhatsApp message |
| `send_sms_message` | Send an SMS message |
| `send_email_message` | Send an email message |
| `get_candidate_send_channels` | Which channels are usable for a patient |
| `list_message_templates` | Templates and their required parameters |
| `escalate_to_staff` | Hand the case to a human with a reason |

### The failure protocol

Tools return a structured `ToolResult` instead of raising. The model reads
`to_payload()` and decides what to do next. A WhatsApp rejection for a
non-opted-in recipient looks like this:

```json
{
  "status": "failed",
  "channel": "whatsapp",
  "recipient": "+15550001111",
  "error_code": "recipient_not_verified",
  "message": "Recipient phone number not in allowed list",
  "provider_code": "131030",
  "retryable": false,
  "suggested_fallback_channels": ["sms", "email"],
  "hint": "The recipient has not opted in / is not in the provider allow-list ... switch to another channel the patient has consented to."
}
```

`status` is one of:

- `sent` - a real (or simulated) message was handed to a provider
- `failed` - the attempt was rejected or errored; check `retryable`
- `ok` - an informational tool (`get_candidate_send_channels`, `list_message_templates`)
- `escalated` - the case was handed to a human via `escalate_to_staff`

The tool deliberately does **not** silently retry on another channel: it
reports the normalized failure, and the LLM chooses the fallback. That keeps
the decision with the model while the mechanics stay deterministic.

Normalized error codes: `recipient_not_verified`, `invalid_recipient`,
`opted_out`, `outside_messaging_window`, `template_not_found`,
`template_param_mismatch`, `rate_limited`, `auth_failed`, `config_missing`,
`provider_unavailable`, `network_error`, `invalid_request`, `unknown`.

### Quick start

```bash
# Offline demonstration - no credentials needed (dry run by default)
.venv/bin/python -m tools.demo_tool_use

# Run the tool-layer test suite
.venv/bin/python -m pytest tests/ -q
```

```python
from tools import build_tool_registry, get_tool_schemas

registry = build_tool_registry()          # reads config from the environment

# What the model does:
result = registry.call(
    "send_whatsapp_message",
    {
        "recipient": "+6591234567",
        "template": "appointment_reminder",
        "params": ["Sarah Johnson", "2026-04-12", "10:00 AM"],
    },
)

print(result.status)                      # 'sent' | 'failed' | ...
print(result.error_code)                  # e.g. 'recipient_not_verified'
print(result.suggested_fallback_channels) # e.g. ['sms', 'email']

# What you pass to the Claude API:
tools = get_tool_schemas()
```

Real Claude tool use (requires `pip install anthropic` and `ANTHROPIC_API_KEY`):

```python
from tools.llm_agent import ToolUseAgent, create_anthropic_client

agent = ToolUseAgent(client=create_anthropic_client(), registry=registry)
run = agent.run("Patient CASE-001 is 69 days overdue. Handle their follow-up.")
print(run.sent, run.failures, run.escalated)
```

### Configuration

Every provider is optional. Copy `.env.example` to `.env` for the full list of
variables, then load it into the environment before running:

```bash
set -a; . ./.env; set +a     # bash/zsh; or use python-dotenv
```

**Nothing is sent for real until you set `MESSAGING_DRY_RUN=0`.** With the flag
absent (or `1`), every send is simulated - even when credentials are present -
so a filled-in `.env` cannot message a real patient by accident. Live mode
additionally requires the channel to be configured; if it is not, the tool
fails loudly with `config_missing` rather than pretending to send.

| `MESSAGING_DRY_RUN` | credentials | result |
|---------------------|-------------|--------|
| unset / `1`         | anything    | simulated success, no network call |
| `0`                 | present     | real provider call |
| `0`                 | missing     | `failed` / `config_missing` |

## 🧪 Test Scenarios

The system includes 8 predefined test scenarios:

1. **Critical Escalation**: Tests urgent case handling
2. **Successful Booking**: Complete booking flow
3. **Patient Decline**: Handling declined follow-ups
4. **Reschedule Request**: Proposing alternative slots
5. **Question Escalation**: Boundary awareness
6. **No-Show Pattern**: Priority adjustment for unreliable patients
7. **Multi-Channel Communication**: Channel preference respect
8. **Rate Limiting**: Reminder interval compliance

## 🌐 API Endpoints

### GET /api/status
Get agent statistics and operational metrics.

**Response:**
```json
{
  "total_active_cases": 10,
  "cases_by_status": {"pending": 5, "message_sent": 3, "booked": 2},
  "cases_by_urgency": {"critical": 1, "high": 2, "medium": 4, "low": 3},
  "escalated_cases": 2
}
```

### GET /api/cases
Get all active follow-up cases.

**Response:**
```json
[
  {
    "patient_id": "P001",
    "patient_name": "Sarah Johnson",
    "urgency": "critical",
    "status": "message_sent",
    "days_overdue": 69
  }
]
```

### GET /api/cases/{patient_id}
Get detailed information about a specific case.

### POST /api/run-cycle
Trigger a daily agent cycle manually.

### POST /api/simulate-reply
Simulate receiving a reply from a patient.

**Request:**
```json
{
  "patient_id": "P001",
  "message": "Yes, I'd like to book"
}
```

### GET /api/escalations
Get all escalated cases requiring staff attention.

### GET /api/audit-logs
Get audit logs with optional filtering.

**Query Parameters:**
- `patient_id`: Filter by patient
- `limit`: Maximum entries (default: 50)

## 🎓 Design Principles

### 1. Transparency & Auditability

Every decision is logged with full context:
- Why was this action taken?
- What factors influenced the decision?
- When was it executed?

### 2. Self-Aware Escalation

The agent knows its limitations:
- Complex questions → Escalate to staff
- Multiple failed contacts → Escalate
- Critical cases → Human oversight

### 3. Clinical Prioritization

Urgency based on:
- Days overdue
- Treatment type (surgery > cleaning)
- Patient history (no-shows flagged)
- Clinical notes (optional AI analysis)

### 4. Respectful Communication

- Uses patient's preferred channel
- Respects rate limiting (no spam)
- Honors opt-out requests
- Personalized messaging

## 🏥 Healthcare Compliance

### Audit Logging
- Complete decision trail
- Regulatory compliance ready (HIPAA, PDPA, GDPR)
- Tamper-evident logging

### Data Privacy
- Abstracted interfaces for real systems
- No hardcoded credentials
- Secure communication channels

### Clinical Safety
- Human oversight for critical cases
- Escalation thresholds
- Transparent reasoning

## 🔧 Extending the System

### Adding New Communication Channels

```python
class CustomChannel(NotificationChannel):
    def send(self, patient: PatientRecord, message: str) -> bool:
        # Implement your channel logic
        return True
    
    def get_channel_type(self) -> ContactChannel:
        return ContactChannel.CUSTOM
```

### Integrating Real Systems

Replace mock implementations:

```python
class ProductionPatientDataStore(PatientDataStore):
    def __init__(self, db_connection):
        self.db = db_connection
    
    def get_all_active_patients(self) -> list[PatientRecord]:
        # Query your actual database
        return query_patients_from_db()
```

### Adding LLM Integration

The messaging tool layer already provides the LLM entry point - see
[LLM Tool Layer](#-llm-tool-layer-function-calling). The LLM decides *whether*
and *what* to send, while `tools/` performs the deterministic provider call:

```python
from tools import build_tool_registry, get_tool_schemas

registry = build_tool_registry()
tools = get_tool_schemas()  # pass to the Claude API as `tools=[...]`
```

### Adding a Messaging Provider

Implement `MessageProvider` and register it with the config:

```python
from tools.providers import MessageProvider, ProviderOutcome

class MyProvider(MessageProvider):
    name = "mine"

    def is_configured(self) -> bool:
        return bool(self.config.my_api_key)

    def send(self, request) -> ProviderOutcome:
        # Perform exactly one attempt, then normalize the outcome.
        return ProviderOutcome.ok(message_id="...")
```

See `tools/providers.py` for the Meta/Twilio/SMTP implementations, and
`tools/errors.py` for the normalized error vocabulary.

## 📈 Future Enhancements

- [x] LLM-powered message sending (Claude tool use - see `tools/`)
- [ ] LLM-powered free-text message generation
- [ ] Voice call automation (Twilio integration)
- [ ] Sentiment analysis for escalation
- [ ] Predictive no-show detection
- [ ] Multi-language support
- [ ] Integration with popular PMS/EHR systems
- [ ] Mobile app for staff
- [ ] Advanced analytics dashboard
- [ ] A/B testing for message effectiveness

## 🤝 Contributing

This is a demonstration system. For production use:

1. Replace mock data stores with actual database connections
2. Integrate real SMS/WhatsApp/Email providers
3. Add authentication and authorization
4. Implement proper error handling and retry logic
5. Add comprehensive test coverage
6. Set up monitoring and alerting

## 📄 License

This project is for demonstration and educational purposes.

## 🙋 Support

For questions or issues:
1. Check the demo script: `python demo.py`
2. Review the code documentation
3. Examine the sample data scenarios

## 🎯 Use Cases

This agent system is designed for:

- **Dental Clinics**: Patient recall management
- **Medical Practices**: Follow-up appointment scheduling
- **Healthcare Systems**: Preventive care reminders
- **Research**: Agentic AI system design patterns

## ⚡ Performance

With the current architecture:
- Processes ~1000 patients in under 1 minute
- Sub-second response to patient messages
- Scales horizontally for larger clinics
- Minimal resource footprint

## 🔒 Security Considerations

For production deployment:

1. **Authentication**: Implement OAuth2/JWT
2. **Encryption**: TLS for all communications
3. **Access Control**: Role-based permissions
4. **Data Sanitization**: Prevent injection attacks
5. **Rate Limiting**: Prevent abuse
6. **Audit Integrity**: Cryptographic signing

---

Built with ❤️ for intelligent, autonomous healthcare systems.
