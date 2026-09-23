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
- **🎯 Perceives Send Failures**: Real AWS/Gmail sending that reports *why* it
  failed and routes around it (channel fallback, then escalation) instead of
  crashing or claiming success
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
│ • Patient List │   │ • Claude (tool  │   │ • Channels  │
│                │   │   use) + rules  │   │ • Fallback  │
│                │   │   as guardrail  │   │ • Escalator │
└────────────────┘   └─────────────────┘   └──────┬──────┘
                              │                   │
                     ┌────────▼────────┐   ┌──────▼──────┐
                     │    OBSERVE      │   │   PERCEIVE  │
                     │                 │◄──│   failures  │
                     │ • Reply Handler │   │ (retry on   │
                     │ • Audit Logger  │   │ other chan.)│
                     └─────────────────┘   └─────────────┘
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
- **`delivery.py`**: `NotificationOutcome` + the delivery backends (offline vs live)
- **`decision.py`**: DECIDE strategies - the rule engine and the Claude engine
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

The agent chooses **one action per case**. Two engines implement the same
interface (`DecisionEngine`); the rules define the *safe* choice space and the
LLM picks inside it.

```python
# The rule engine computes what is permissible, most-preferred first
permissible = rule_engine.permissible_actions(context)   # the guardrail

# With ANTHROPIC_API_KEY set, Claude chooses from that list via a
# `choose_next_action` tool call. Its answer is validated against the same list,
# and anything out of bounds falls back to the rules.
action = decide_for_case(case, today)   # -> ActionDecision(source=...)
```

`ActionDecision.source` records where the decision came from: `rules`, `llm`,
`llm-guardrail` (the model answered out of bounds), `llm-error` (the API call
failed) or `llm-disabled` (no key/package). The audit log keeps it, so a
reviewer can always tell whether a human-facing choice was made by a model.

### Phase 3: ACT

The agent executes the decision, **and observes whether it worked**:

```python
# Send reminder, falling back across channels on failure
outcome = deliver_with_fallback(case, message)
if not outcome.success:
    # every reachable channel failed -> escalate, do not report success
    escalate_undeliverable(case, outcome, today)

# Book appointment
scheduler.try_book(case)

# Escalate to staff -> records the case AND emails AGENT_ESCALATION_EMAIL
escalation_handler.escalate(case, reason)
```

A send is never assumed to have worked. Each attempt returns a
`NotificationOutcome` carrying `success`, `error_code`, `error_message`,
`retryable`, `provider_code` and the provider's `suggested_fallbacks`. The
agent tries the patient's other reachable channels in preference order, and if
they all fail it escalates the case instead of silently moving on.

### Phase 4: OBSERVE

The agent processes feedback **and its own failures**:

```python
# Handle patient reply
action, context = conversation_manager.handle_reply(case, message)

# Update case state
case.status = new_status

# Log for audit
audit_logger.log_decision(case, action, rationale)
```

Observed failures are remembered in `agent.undelivered` and are *not* retried on
a channel that already failed, so a dead number is not hammered every cycle.
State that must survive across days (the reminder count, the status, the
conversation log) is carried forward from the previous cycle, because each cycle
rebuilds cases from the data store.

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

## 🤖 Agent Decisions and Safety Gates

### Can it decide on its own?

Yes, within limits the rules define. Two things are deliberately separated:

| Question | Answered by | Why |
|---|---|---|
| *Which* actions are safe for this case? | `RuleDecisionEngine.permissible_actions` | Clinical safety must not depend on a model being reachable or correct |
| *Which* of those to take now? | Claude, via a `choose_next_action` tool call | Judgement (tone, timing, when to involve a person) is what a model is good at |

The LLM can **never** take an action the rules did not offer. `ActionDecision.source`
records which engine actually chose, so every decision is auditable.

### What happens when a send fails?

This is the behaviour the whole design exists for. A send returns a
`NotificationOutcome`, never an exception:

| Situation | `error_code` | What the agent does |
|---|---|---|
| Recipient not verified / not on the allow-list | `recipient_not_verified` | Try the patient's next reachable channel; escalate if none work |
| Missing contact detail for that channel | `missing_recipient` | Skip that channel, try the next |
| No automated sender exists (e.g. voice call) | `unsupported_channel` | Try the next channel |
| Account not onboarded to the service | `not_subscribed` | Try the next channel, then escalate -- a human must fix this |
| Provider/network problem | `provider_unavailable`, `network_error` | Try the next channel |
| Body or subject empty | `invalid_request` | Treat as a bug; recorded, not retried forever |

The agent **never** reports a reminder as sent when it was not, and a case is
never abandoned: once the reminder budget
(`ClinicPolicyConfig.max_reminders_before_escalation`) is spent, or every channel
has failed, the case is escalated and staff are alerted.

### The two-switch live gate

Real sends require **both** switches, so a populated `.env` can never make
`python demo.py` message a real patient:

```bash
MESSAGING_DRY_RUN=0   # tools layer: allow real transmission
AGENT_LIVE_SENDS=1    # agent layer: I mean it for the agent too
```

With either one missing, the agent uses `PrintDeliveryBackend` and prints what it
would have sent. See `is_configured_for_live_sends()` in `agent/delivery.py`.

### Enabling Claude for DECIDE

```bash
pip install anthropic
export ANTHROPIC_API_KEY=sk-ant-...
export AGENT_DECISION_MODEL=claude-sonnet-4-5   # optional
```

With no key the agent runs rules-only -- identical behaviour, no error. The web
app already uses this path (`FollowUpAgentOrchestrator.with_llm_decisions`), so
setting the key is the only step needed.

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
WhatsApp Business API / Twilio / Amazon SES / AWS End User Messaging
```

### Available tools

| Tool | Purpose |
|------|---------|
| `send_whatsapp_message` | Send a templated WhatsApp message |
| `send_sms_message` | Send an SMS message |
| `send_email_message` | Send an email message |
| `send_sms` | Send an SMS through AWS End User Messaging (allow-listed) |
| `send_email` | Send an email through Amazon SES (allow-listed) |
| `get_candidate_send_channels` | Which channels are usable for a patient |
| `list_message_templates` | Templates and their required parameters |
| `escalate_to_staff` | Hand the case to a human with a reason |

#### `send_sms` and `send_email` (real AWS delivery)

These two are the production transports. They take a `reason` -- the agent's
justification -- which is printed to stdout as `[Agent决策] ...`, written to the
audit trail and echoed back in `data.reason`.

```python
registry.call("send_sms", {
    "phone_number": "+6583536885",
    "message": "Hi! This is Bright Smile: time for your 6-month check-up.",
    "reason": "patient is 7 months past their last visit",
})
# -> {'status': 'sent', 'message_id': '...'}   on success
# -> {'status': 'failed', 'error': '...'}      plus the guidance fields below

registry.call("send_email", {
    "to_email": "martinchenonly1@gmail.com",
    "subject": "Time for your check-up",
    "body": "Our records show it has been a while since your last visit.",
    "reason": "no reply to the SMS reminder",
})
```

Both declare `phone_number` / `to_email` as **required** in their schema so the
contract does not change when real patient data arrives. Today, however, the
clinic's AWS account is still sandboxed, so the tools refuse to transmit to
anything outside the verified-recipient allow-list (`AWS_SMS_ALLOWED_NUMBERS` /
`AWS_EMAIL_ALLOWED_ADDRESSES`, defaulting to one verified phone number and one
verified email). A refused recipient comes back as `recipient_not_verified`
with `provider_code: "allowlist"` -- the same shape the model already knows how
to fall back from, so it will switch channel instead of crashing.

Delivery notes:

- `send_sms` calls `pinpoint-sms-voice-v2.send_text_message` with
  `MessageType="TRANSACTIONAL"`; the account must be onboarded to AWS End User
  Messaging SMS first, otherwise it returns `not_subscribed`.
- `send_email` calls `ses.send_email` from `AWS_SES_SOURCE`, which must be an
  SES-verified identity.
- Both require `boto3` (`pip install boto3`). Without it they report
  `config_missing` -- they never raise `ImportError`.
- Live sending stays opt-in: `MESSAGING_DRY_RUN=0`.

### Proving a message was delivered

An SES `MessageId` only means *accepted*: it says nothing about whether the
message reached the inbox. To close that gap, set `AWS_SES_CONFIGURATION_SET` to
an SES configuration set with an event destination. Every send is then tagged
with it and SES emits per-send `Delivery` / `Bounce` / `Complaint` events.

This project ships one for the verified test address:
`patient-followup`, with an SNS event destination publishing to
`ses-delivery-events` (email subscription to `martinchenonly1@gmail.com`).

```bash
export AWS_SES_CONFIGURATION_SET=patient-followup
```

Events are also readable programmatically from CloudWatch -- namespace
`AWS/SES`, metric `Delivery` / `Bounce` / `Complaint`, dimension
`ses:configuration-set=patient-followup`:

```bash
aws cloudwatch get-metric-statistics \
  --namespace AWS/SES --metric-name Delivery --period 3600 --statistics Sum \
  --start-time "$(date -u -v-1H +%Y-%m-%dT%H:%M:%SZ)" \
  --end-time "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --dimensions Name=ses:configuration-set,Value=patient-followup
```

The SMTP channel needs no configuration set: the mailbox itself is the proof.
One IMAP login shows whether the message landed in `INBOX`, in `Spam`, or
nowhere -- the same credentials, a different port:

```bash
.venv/bin/python - <<'PY'
import imaplib
from tools.config import MessagingConfig
from tools.tls import default_ssl_context

cfg = MessagingConfig.from_env()
imap = imaplib.IMAP4_SSL("imap.gmail.com", 993,
                         ssl_context=default_ssl_context())
imap.login(cfg.smtp_username, cfg.smtp_password)
for folder in ("INBOX", '"[Gmail]/Spam"'):
    imap.select(folder, readonly=True)
    _, found = imap.search(None, 'SUBJECT "BrightSmile"')
    print(folder, len(found[0].split()))
imap.logout()
PY
```

Note that Gmail's non-INBOX folder names are modified UTF-7 (`[Gmail]/&V4NXPpCuTvY-`
is the spam folder in a Chinese-locale account), so search by subject rather than
assuming an English folder name.

### Deliverability

`AWS_SES_SOURCE` defaults to the recipient's own `gmail.com` address. That works
for smoke tests but is bad for placement: the message claims to be from
`@gmail.com` while being relayed by SES, so it carries **no Gmail DKIM
signature**. Gmail's DMARC policy is `p=none`, so it is not rejected -- but it is
filed as spam (observed: delivered, but into the spam folder).

Fixing that properly means sending from a domain you own, which SES then signs
with that domain's DKIM key: `scripts/ses_domain_setup.py` creates the identity
and prints the DNS records to add.

```bash
.venv/bin/python scripts/ses_domain_setup.py clinic.example.com --create
# add the printed records at your DNS provider, wait, then:
.venv/bin/python scripts/ses_domain_setup.py clinic.example.com --check
export AWS_SES_SOURCE=reminders@clinic.example.com
```

That route needs **control of the domain's DNS**. Where there is none -- a
hackathon account, a shared domain -- use the SMTP channel instead, with the
mailbox's own provider as the relay. Mail sent through Gmail's SMTP is signed by
Google, so it lands in the inbox with no DNS work at all. The
`send_email_message` tool already speaks SMTP:

```bash
export SMTP_HOST=smtp.gmail.com
export SMTP_PORT=587
export SMTP_USE_TLS=1
export SMTP_USERNAME=martinchenonly1@gmail.com
export SMTP_PASSWORD=your-16-character-app-password   # not your account password
export EMAIL_FROM=martinchenonly1@gmail.com
export MESSAGING_DRY_RUN=0
```

Gmail rejects account passwords over SMTP: enable 2-Step Verification, then
create an App Password at <https://myaccount.google.com/apppasswords>. That
password is **exactly 16 characters**. Gmail displays it grouped
(`abcd efgh ijkl mnop`), and either form works here: `MessagingConfig` drops the
display spaces, but only when the value is unambiguously an app password --
exactly 16 alphanumerics once whitespace is removed -- so a conventional
password containing spaces is passed through untouched. A grouped password with
the wrong number of characters is left alone and logs a plain
`auth_failed`; sending the spaced form to Gmail makes it drop the connection
mid-handshake instead, which reads as a provider outage.

Two caveats apply to the SES path while the account is in the sandbox: every
recipient must be a verified identity, and you can only send from verified
identities, so a domain must reach `verified for sending: True` before it can be
used as `AWS_SES_SOURCE`.

### TLS trust stores

Providers build their TLS contexts through `tools/tls.py`, which prefers a CA
bundle that actually exists: `SSL_CERT_FILE`, then `certifi`, then the system
store. This matters because `ssl.create_default_context()` reads the *system*
store, and a macOS python.org install leaves it empty until `Install
Certificates.command` is run -- so every live send fails with
`CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate`, which looks
exactly like a provider outage. Set `SSL_CERT_FILE` to override the bundle.

One operational gotcha: if your AWS CLI is signed in with `aws login` (a
`login_session` entry in `~/.aws/config`), **botocore cannot read it** and both
tools will report `config_missing` with `NoCredentialsError` even though the CLI
works. Export the credentials the SDK can consume:

```bash
eval "$(aws configure export-credentials --export-env)"
# or, without exporting into your shell:
.venv/bin/python - <<'PY'
import json, os, subprocess
creds = json.loads(subprocess.check_output(
    ["aws", "configure", "export-credentials"], text=True))
os.environ.update({
    "AWS_ACCESS_KEY_ID": creds["AccessKeyId"],
    "AWS_SECRET_ACCESS_KEY": creds["SecretAccessKey"],
    "AWS_SESSION_TOKEN": creds["SessionToken"],
})
PY
```

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
`not_subscribed`, `provider_unavailable`, `network_error`, `invalid_request`,
`unknown`.

`not_subscribed` is specific to the AWS tools: the account is not onboarded to
the service, which the agent cannot fix, so it is non-retryable with no
fallback channels and the hint tells the model to escalate to staff.

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

The AWS tools are the one exception to "credentials are enough": because the
account is sandboxed, `send_sms` / `send_email` also require the recipient to
be on the verified allow-list. Dry-run additionally skips client construction
entirely, so `send_sms` and `send_email` succeed in dry run even without
`boto3` installed.

To check whether the account can really deliver an SMS:

```bash
aws pinpoint-sms-voice-v2 describe-account-attributes --region ap-southeast-1
# SubscriptionRequiredException -> send_sms will return error_code 'not_subscribed'
```

and for SES (a verified-identity list and sandbox status):

```bash
aws ses get-identity-verification-attributes \
    --identities martinchenonly1@gmail.com --region ap-southeast-1
aws ses get-account-sending-enabled --region ap-southeast-1
```

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
