# System Architecture

## Project Structure

```
agent_followship/
├── README.md                    # Main documentation
├── QUICKSTART.md               # 5-minute setup guide
├── ARCHITECTURE.md             # This file - system architecture
├── LICENSE                     # MIT License
├── requirements.txt            # Python dependencies
├── .gitignore                 # Git ignore patterns
│
├── Core Agent System
│   ├── models.py              # Data models and enums
│   ├── actions.py             # Agent action definitions
│   ├── config.py              # Configuration settings
│   ├── data_access.py         # Data store and calendar interfaces
│   ├── business_rules.py      # Rule engine and urgency scoring
│   ├── notifications.py       # Multi-channel messaging
│   ├── conversation.py        # Intent recognition and conversation management
│   ├── action_handlers.py     # Scheduler, escalation, audit logging
│   └── orchestrator.py        # Main agentic loop orchestrator
│
├── Web Application
│   ├── app.py                 # Flask application and REST API
│   └── templates/
│       └── dashboard.html     # Web dashboard interface
│
├── Demo and Testing
│   ├── sample_data.py         # Sample data generator
│   └── demo.py                # Interactive demonstration
│
├── LLM Tool Layer (tools/)
│   ├── result.py              # ToolResult - never-raising return type
│   ├── errors.py              # Error taxonomy + channel fallback table
│   ├── config.py              # Env-driven MessagingConfig + templates
│   ├── transport.py           # HTTP/SMTP transports + fake test doubles
│   ├── providers.py           # MessageProvider: Meta, Twilio, SMTP
│   ├── messaging.py           # Deterministic tools + ToolRegistry
│   ├── schemas.py             # Anthropic tool schemas
│   ├── llm_agent.py           # Claude tool-use loop + AgentRun
│   └── demo_tool_use.py       # Offline 3-scenario demonstration
│
├── Tests (tests/)
│   ├── conftest.py            # Shared fixtures (env, registry)
│   ├── test_error_taxonomy.py # Provider code -> normalized error map
│   ├── test_tool_contract.py  # ToolResult invariants, "never raises"
│   ├── test_providers.py      # Meta/Twilio/SMTP payloads + classification
│   └── test_agent_loop.py     # Tool-use loop, fallback, escalation, audit
│
├── .env.example               # Environment template (copy to .env)
│
└── Data (generated at runtime)
    └── audit_log.json         # Audit trail storage
```

## Component Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                        Web Dashboard (Flask)                     │
│                                                                  │
│  ┌────────────┐  ┌────────────┐  ┌────────────┐  ┌──────────┐ │
│  │  Case View │  │ Statistics │  │ Escalations│  │  Logs    │ │
│  └────────────┘  └────────────┘  └────────────┘  └──────────┘ │
└──────────────────────────┬───────────────────────────────────────┘
                           │ REST API
┌──────────────────────────▼───────────────────────────────────────┐
│              FollowUpAgentOrchestrator (Brain)                   │
│                                                                   │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │              Agentic Loop Phases                          │  │
│  │                                                            │  │
│  │  1. PERCEIVE: Identify overdue patients                   │  │
│  │     ↓                                                      │  │
│  │  2. DECIDE: Score urgency & prioritize                    │  │
│  │     ↓                                                      │  │
│  │  3. ACT: Execute actions (message/book/escalate)          │  │
│  │     ↓                                                      │  │
│  │  4. OBSERVE: Process replies & update state               │  │
│  │     ↓                                                      │  │
│  │     └──── (Loop back to PERCEIVE) ────┘                   │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                   │
│  Component Integration:                                          │
│  ┌─────────────┐  ┌──────────────┐  ┌─────────────────┐       │
│  │ Rule Engine │  │ Urgency      │  │ Conversation    │       │
│  │             │  │ Scorer       │  │ Manager         │       │
│  └─────────────┘  └──────────────┘  └─────────────────┘       │
│                                                                   │
│  ┌─────────────┐  ┌──────────────┐  ┌─────────────────┐       │
│  │ Message     │  │ Notification │  │ Appointment     │       │
│  │ Composer    │  │ Channels     │  │ Scheduler       │       │
│  └─────────────┘  └──────────────┘  └─────────────────┘       │
│                                                                   │
│  ┌─────────────┐  ┌──────────────┐                             │
│  │ Escalation  │  │ Audit        │                             │
│  │ Handler     │  │ Logger       │                             │
│  └─────────────┘  └──────────────┘                             │
└───────────────────────────┬───────────────────────────────────────┘
                            │
┌───────────────────────────▼───────────────────────────────────────┐
│                    Data Access Layer                              │
│                                                                    │
│  ┌──────────────────────┐        ┌──────────────────────┐       │
│  │  Patient Data Store  │        │ Calendar Integration │       │
│  │                      │        │                      │       │
│  │  • Get patients      │        │  • Find slots        │       │
│  │  • Update records    │        │  • Book appointment  │       │
│  │  • Track contacts    │        │  • Cancel booking    │       │
│  └──────────────────────┘        └──────────────────────┘       │
└────────────────────────────────────────────────────────────────────┘
```

## Data Flow

### 1. Daily Cycle Initialization

```
User/Scheduler
    │
    ├─→ orchestrator.run_daily_cycle()
    │
    ├─→ data_store.get_all_active_patients()
    │   └─→ Returns: [PatientRecord, ...]
    │
    ├─→ rule_engine.compute_overdue_patients(patients, today)
    │   └─→ Returns: [FollowUpCase, ...]
    │
    ├─→ urgency_scorer.score(case)
    │   └─→ Returns: UrgencyLevel
    │
    ├─→ urgency_scorer.sort_by_urgency(cases)
    │   └─→ Returns: Prioritized [FollowUpCase, ...]
    │
    └─→ For each case:
        ├─→ decide_action_for_case(case)
        │   └─→ Returns: AgentAction
        │
        └─→ execute_action(case, action)
            ├─→ message_composer.compose(case)
            ├─→ channel.send(patient, message)
            └─→ audit_logger.log_decision(...)
```

### 2. Patient Reply Processing

```
Patient Reply
    │
    ├─→ orchestrator.handle_incoming_reply(patient_id, message)
    │
    ├─→ Get case from active_cases
    │
    ├─→ conversation_manager.handle_reply(case, message)
    │   ├─→ recognize_intent(message)
    │   │   └─→ Returns: (intent, context)
    │   │
    │   └─→ intent_to_action(intent, case, context)
    │       └─→ Returns: AgentAction
    │
    ├─→ Execute decided action:
    │   ├─→ CONFIRM_BOOKING → scheduler.try_book(case)
    │   ├─→ PROPOSE_SLOT → scheduler.find_available_slots()
    │   ├─→ ESCALATE → escalation_handler.escalate(case)
    │   └─→ MARK_DECLINED → Update case status
    │
    ├─→ conversation_manager.generate_response(action, case)
    │   └─→ Returns: response_message
    │
    ├─→ channel.send(patient, response_message)
    │
    └─→ audit_logger.log_communication(...)
```

## Module Dependencies

```
orchestrator.py
    ├── models.py (PatientRecord, FollowUpCase, Enums)
    ├── config.py (ClinicPolicyConfig)
    ├── data_access.py (PatientDataStore, CalendarIntegration)
    ├── business_rules.py (RecallRuleEngine, UrgencyScorer)
    ├── notifications.py (Channels, MessageComposer)
    ├── conversation.py (ConversationManager)
    ├── action_handlers.py (Scheduler, Escalation, Audit)
    └── actions.py (AgentAction)

app.py
    ├── orchestrator.py
    ├── data_access.py
    ├── models.py
    ├── config.py
    └── sample_data.py

demo.py
    ├── orchestrator.py
    ├── data_access.py
    ├── config.py
    └── sample_data.py

tools/                        # LLM tool layer (self-contained, no imports from agent/)
    ├── result.py             # ToolResult - never-raising return type
    ├── errors.py             # Error taxonomy + channel fallback table + AWS code map
    ├── config.py             # Env-driven MessagingConfig + templates + allow-lists
    ├── transport.py          # HTTP/SMTP transports + fake test doubles
    ├── providers.py          # MessageProvider: Meta, Twilio, SMTP
    ├── aws_providers.py      # MessageProvider: AWS End User Messaging SMS, SES
    ├── messaging.py          # 8 tools + ToolRegistry dispatcher
    ├── schemas.py            # Anthropic tool schemas
    ├── llm_agent.py          # Claude tool-use loop + AgentRun
    └── demo_tool_use.py      # Offline 3-scenario demonstration
```

## LLM Tool Layer

The tool layer inverts control relative to the orchestrator: instead of
`orchestrator.py` calling `notifications.py` directly, a **model** chooses which
tool to call and the deterministic tool performs exactly one provider attempt.

```
ToolUseAgent (tools/llm_agent.py)
    │  1. send system prompt + tool schemas to Claude
    │  2. Claude replies with text and/or tool_use blocks
    ▼
ToolRegistry.call(name, args)        # tools/messaging.py
    │  always returns a ToolResult - never raises
    ▼
MessagingToolkit._send()             # one attempt, one provider
    ▼
MessageProvider.send()               # Meta/Twilio/SMTP/AWS
    ▼
Transport (urllib / smtplib / boto3)
```

### Why the tools never raise

A provider rejection such as "recipient not verified" is normal traffic, not an
exception. If the tool raised, the tool-use loop would break and the model would
never learn why. Instead the outcome is normalized:

```
provider error (HTTP 400 + code 131030)
    → SendErrorCode.RECIPIENT_NOT_VERIFIED
    → ToolResult(status="failed", retryable=False,
                 suggested_fallback_channels=["sms", "email"],
                 hint="WhatsApp requires an opted-in recipient. Try SMS or email.")
    → returned to the model as the tool_result payload
    → model decides: retry on SMS, email the patient, or escalate_to_staff
```

Layer by layer:

| Layer | Responsibility | Never raises? |
|-------|----------------|---------------|
| `transport.py` | Perform I/O; map `HTTPError`/`URLError`/`SMTPException` to a response | Yes, converts to a response object |
| `providers.py` | Build the provider request, classify the response | Yes, returns `ProviderOutcome` |
| `aws_providers.py` | Build the AWS request, classify `ClientError` | Yes, returns `ProviderOutcome` |
| `messaging.py` | Validate input, perform one attempt, build `ToolResult` | Yes, outer `except Exception` → `_unexpected` |
| `llm_agent.py` | Dispatch `tool_use` blocks, feed results back | Yes, unknown tool → error payload |

### AWS-backed channels

`send_sms` and `send_email` share the logical `sms` / `email` channels with the
Twilio and SMTP providers, but route to dedicated providers in
`aws_providers.py` selected by the toolkit, so the two paths never interfere.
They add three things the other send tools do not have:

1. **A `reason` argument** - the agent's justification, printed as
   `[Agent决策] ...`, stored in the audit entry and echoed in
   `ToolResult.data["reason"]`. Recorded on success, on refusal and on provider
   failure alike, because the audit question is "why did we try this".
2. **A verified-recipient allow-list** - `AWS_SMS_ALLOWED_NUMBERS` /
   `AWS_EMAIL_ALLOWED_ADDRESSES`, enforced *before* any client is built and
   failing **closed**. A sandboxed account can only deliver to verified
   destinations, so refusing early gives the model the same
   `recipient_not_verified` shape it already knows how to fall back from
   (`provider_code: "allowlist"` distinguishes it from a provider rejection).
3. **Structured `ClientError` handling** - `errors.from_boto_error()` maps the
   raw AWS code string through `AWS_ERROR_CODES` into the shared taxonomy.
   `SubscriptionRequiredException` becomes `not_subscribed` (non-retryable, no
   fallbacks - an operator must onboard the account), SES's `MessageRejected`
   becomes `recipient_not_verified`, throttling becomes `rate_limited`, and
   anything unrecognized becomes `unknown` with the raw code preserved in
   `provider_code`. Exceptions that are *not* AWS-shaped are treated as bugs and
   routed through `_guard()` so they are never mislabelled.

`boto3` is imported lazily and is an optional dependency: without it both tools
return `config_missing`, and the offline demo, the tests and every other tool
keep working. Dry-run mode short-circuits before the client is resolved, so no
session is created and no credentials are read.

`send_email` also honours the optional `AWS_SES_CONFIGURATION_SET`. Without it
SES reports only that the message was *accepted* (`MessageId`), which is not the
same as *delivered*; with it SES emits per-send `Delivery` / `Bounce` /
`Complaint` events to the configured destination (CloudWatch metrics and an SNS
topic), so delivery can be verified rather than assumed.

`AWS_SES_SOURCE` selects the sender identity, and is the lever for
deliverability. Sending from an address whose domain SES cannot sign produces
mail with no DKIM signature for its own From domain, which inbox providers file
as spam; sending from an SES-verified domain does not. Because the sender is
configuration rather than code, switching to an owned domain is an environment
change -- `scripts/ses_domain_setup.py` creates the identity and prints the DNS
records to publish.

### Dry-run semantics

Live sending is **opt-in**. `MESSAGING_DRY_RUN=0` is the only thing that permits
transmission, and even then only for a channel that is configured - an
unconfigured channel in live mode fails loudly with `config_missing` instead of
pretending to send. With the flag unset (or `1`), every send is simulated
regardless of credentials, so an accidentally populated `.env` cannot message a
real patient. `MessagingConfig.simulates` / `.live_requested` expose the
decision to the channel-listing tool.

The `.env` file is not read implicitly: load it into the environment first
(`set -a; . ./.env; set +a`) or pass an explicit mapping to
`MessagingConfig.from_env()`.

### Relation to the existing modules

`tools/` is additive and does not import `agent/`, `core/`, or `utils/`. It is
the intended replacement for the mock sending path in `agent/notifications.py`
once the orchestrator is rewired; the schemas (`tools/schemas.py`) and the
registry (`tools/messaging.py`) are the only surfaces a caller needs.

## Design Patterns

### 1. Strategy Pattern
**Where:** Notification channels (`notifications.py`)
**Why:** Different messaging strategies (SMS, WhatsApp, Email) share common interface

### 2. Template Method Pattern
**Where:** Message composition (`notifications.py`)
**Why:** Common message structure with customizable content

### 3. Observer Pattern
**Where:** Audit logging (`action_handlers.py`)
**Why:** Log events without tight coupling to business logic

### 4. Factory Pattern
**Where:** Case creation (`business_rules.py`)
**Why:** Centralized creation of FollowUpCase objects with proper initialization

### 5. Facade Pattern
**Where:** Orchestrator (`orchestrator.py`)
**Why:** Simplified interface to complex subsystems

### 6. Abstract Factory Pattern
**Where:** Data access layer (`data_access.py`)
**Why:** Abstract interfaces allow swapping mock/production implementations

## State Machine

### Case Status Transitions

```
        ┌─────────┐
        │ PENDING │ (Initial state)
        └────┬────┘
             │
             ├─→ run_daily_cycle()
             │
        ┌────▼────────────┐
        │ MESSAGE_SENT    │
        └────┬────────────┘
             │
             ├─→ Patient replies
             │
        ┌────▼────────────┐
        │ AWAITING_REPLY  │
        └────┬────────────┘
             │
             ├─→ Confirms → BOOKED
             ├─→ Declines → DECLINED
             ├─→ Questions → ESCALATED
             └─→ No response + max reminders → ESCALATED
```

## Scalability Considerations

### Current Architecture (Single Instance)
- Handles ~1,000 patients efficiently
- In-memory data structures
- Single Flask process

### Scaling to 10,000+ Patients
1. **Database**: Replace MockPatientDataStore with PostgreSQL/MongoDB
2. **Message Queue**: Add Redis/RabbitMQ for async processing
3. **Caching**: Implement Redis caching for case data
4. **Load Balancing**: Multiple Flask instances behind nginx
5. **Background Workers**: Celery for scheduled cycles

### Scaling to 100,000+ Patients
1. **Microservices**: Split into separate services
   - Case Management Service
   - Notification Service
   - Scheduling Service
   - Analytics Service
2. **Distributed Database**: Sharding by clinic/region
3. **Event Streaming**: Kafka for event processing
4. **Container Orchestration**: Kubernetes deployment
5. **CDN**: CloudFront for dashboard assets

## Security Architecture

### Current Implementation (Demo)
- No authentication
- Mock data only
- Local file logging

### Production Requirements
1. **Authentication & Authorization**
   - OAuth2/OpenID Connect
   - Role-based access control (RBAC)
   - API key management

2. **Data Encryption**
   - TLS 1.3 for all communications
   - Encrypted database fields (PHI)
   - Encrypted audit logs

3. **Compliance**
   - HIPAA compliance logging
   - PHI access tracking
   - Data retention policies
   - Breach notification mechanisms

4. **Network Security**
   - VPC isolation
   - WAF (Web Application Firewall)
   - DDoS protection
   - Rate limiting

## Testing Strategy

### Unit Tests
- Each module has isolated tests
- Mock external dependencies
- High code coverage (>80%)

Tests live in `tests/` at the repository root. The tool-layer suite needs no
credentials or network access: `FakeTransport` / `FakeSmtpConnection` record
requests, and `ScriptedModelClient` replaces the Claude API with a deterministic
script, so the whole tool-use loop is exercised offline.

```bash
.venv/bin/python -m pytest tests/ -q          # 179 tests
.venv/bin/python -m tools.demo_tool_use       # 3 scenarios, 11 checks
```

### Integration Tests
- Test component interactions
- Mock external APIs
- Database transactions

### End-to-End Tests
- Full workflow validation
- Sample data scenarios
- API endpoint testing

### Performance Tests
- Load testing (concurrent users)
- Stress testing (data volume)
- Response time validation

## Monitoring & Observability

### Metrics to Track
- Cases processed per day
- Average response time
- Escalation rate
- Booking success rate
- System uptime

### Logging Levels
- DEBUG: Development troubleshooting
- INFO: Operational events
- WARNING: Potential issues
- ERROR: Errors requiring attention
- CRITICAL: System failures

### Alerting Triggers
- Escalation threshold exceeded
- API error rate > 5%
- Response time > 2 seconds
- Audit log write failures
- Database connection issues

## Deployment

### Development
```bash
python app.py
# Or
python demo.py
```

### Production (Example with Gunicorn)
```bash
gunicorn -w 4 -b 0.0.0.0:8000 app:app
```

### Docker (Future)
```dockerfile
FROM python:3.9-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
CMD ["gunicorn", "-w", "4", "-b", "0.0.0.0:8000", "app:app"]
```

### Kubernetes (Future)
- Deployment with replicas
- Service for load balancing
- Ingress for external access
- ConfigMap for configuration
- Secret for credentials

---

**Version:** 1.0.0  
**Last Updated:** 2024  
**Architecture Type:** Monolithic (ready for microservices migration)
