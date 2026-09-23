# System Architecture

## Project Structure

```
agent_followship/
├── README.md                    # Main documentation
├── QUICKSTART.md               # 5-minute setup guide
├── ARCHITECTURE.md             # This file - system architecture
├── LICENSE                     # MIT License
├── requirements.txt            # Python dependencies
├── .gitignore                  # Git ignore patterns
├── demo.py                     # Interactive demonstration
├── hospital_setup.py           # THE hospital entry point: BYO LLM + BYO mailbox
├── .env.example                # Environment template (copy to .env)
│
├── core/                       # Domain types, no behaviour
│   ├── models.py               # PatientRecord, FollowUpCase, enums
│   ├── actions.py              # AgentAction
│   ├── config.py               # ClinicPolicyConfig
│   └── data_access.py          # PatientDataStore / CalendarIntegration (+ mocks)
│
├── agent/                      # The agentic loop
│   ├── business_rules.py       # RecallRuleEngine, UrgencyScorer
│   ├── conversation.py         # Intent recognition, ConversationManager
│   ├── notifications.py        # Channel classes + build_notification_channels()
│   ├── delivery.py             # NotificationOutcome, offline vs live backends
│   ├── decision.py             # RuleDecisionEngine, LlmDecisionEngine
│   ├── action_handlers.py      # Scheduler, EscalationHandler, AuditLogger
│   └── orchestrator.py         # FollowUpAgentOrchestrator: PERCEIVE/DECIDE/ACT/OBSERVE
│
├── web/                        # Web application
│   ├── app.py                  # Flask application and REST API
│   └── templates/
│       └── dashboard.html      # Web dashboard interface
│
├── utils/
│   ├── sample_data.py          # Sample data generator
│   └── llm_parser.py           # Flexible patient-list import
│
├── tools/                      # LLM tool layer (function calling)
│   ├── result.py               # ToolResult - never-raising return type
│   ├── errors.py               # Error taxonomy + channel fallback table
│   ├── config.py               # Env-driven MessagingConfig + templates
│   ├── tls.py                  # CA bundle / SSL context resolution
│   ├── transport.py            # HTTP/SMTP transports + fake test doubles
│   ├── providers.py            # MessageProvider: Meta, Twilio, SMTP
│   ├── aws_providers.py        # MessageProvider: End User Messaging SMS, SES
│   ├── messaging.py            # Deterministic tools + ToolRegistry
│   ├── schemas.py              # Anthropic tool schemas
│   ├── llm_agent.py            # Claude tool-use loop + AgentRun
│   ├── llm_providers.py        # BYO LLM: Anthropic/OpenAI/Azure/Ollama adapters
│   └── demo_tool_use.py        # Offline 3-scenario demonstration
│
├── scripts/
│   └── ses_domain_setup.py     # Create/verify an SES sending domain
│
└── tests/
    ├── conftest.py             # Shared fixtures (env, registry)
    ├── test_error_taxonomy.py  # Provider code -> normalized error map
    ├── test_tool_contract.py   # ToolResult invariants, "never raises"
    ├── test_providers.py       # Meta/Twilio/SMTP payloads + classification
    ├── test_aws_tools.py       # AWS SES / End User Messaging via fake clients
    ├── test_agent_loop.py      # Tool-use loop, fallback, escalation, audit
    ├── test_agent_delivery.py  # The agent's own failure handling + decisions
    ├── test_llm_providers.py   # BYO-LLM translation, live calls, factories
    ├── test_hospital_setup.py  # The clinic-facing config file and its checks
    └── test_web_upload.py      # Upload -> import -> dashboard round trip

Generated at runtime: `audit_log.json` (audit trail), `escalations.json`.
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
        ├─→ _carry_forward(previous_case, case)
        │   └─→ reminder_count / status / log survive the rebuild
        │
        ├─→ _decide_for_case(case, today)
        │   ├─→ decision_engine describes the case (DecisionContext)
        │   ├─→ RuleDecisionEngine.permissible_actions(context)  # guardrail
        │   ├─→ LlmDecisionEngine picks within that set (when configured)
        │   └─→ Returns: ActionDecision(action, rationale, source)
        │
        └─→ _execute_action(case, decision, today)
            ├─→ message_composer.compose(case)
            ├─→ _deliver_with_fallback(case, message)         # ACT
            │   ├─→ for each reachable, not-yet-failed channel:
            │   │     channel.send(patient, message)
            │   │     └─→ Returns: NotificationOutcome  (never raises)
            │   └─→ Returns: the successful outcome, or the last failure
            ├─→ on total failure: _escalate_undeliverable(case, outcome, today)
            └─→ audit_logger.log_decision(...) / log_communication(...)
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

### 3. Patient-list import (upload → dashboard)

```
File upload (CSV / TSV / JSON / TXT / XLSX via openpyxl; not legacy .xls)
    │
    ▼
POST /api/upload-patient-list            web/app.py
    └─→ LLMPatientParser.parse_file()    utils/llm_parser.py
          ├─ format-specific reader, then _standardize_patient_data()
          ├─ daysoverdue = max(0, (today - last_visit_date) - recall_interval_days)
          └─→ preview rows (nothing is stored yet)
    │
    ▼
POST /api/import-patients
    ├─→ data_store.add_patient()  for each row   (skips duplicates by patient_id)
    └─→ agent.run_daily_cycle()   <-- this is what makes the rows visible
          └─→ PERCEIVE finds the newly-overdue patients and creates cases
    │
    ▼
GET /api/status, GET /api/cases  ->  dashboard re-renders

Two independent failure modes, worth knowing when a list "does not show up":
  - the parse step stored nothing by design (preview only), so a client that
    calls only /api/upload-patient-list will never see cases; and
  - a row with no `last_visit` gets days_overdue = 0, so it will appear in the
    store but produce no case until it is genuinely overdue.
```

Note: `web/app.py` constructs `LLMPatientParser(use_llm=False)`, so import is
entirely rule-based (`use_llm=True` plus a key is what enables the optional model
assist). The import path needs no credentials and no network.

## The hospital interface (`hospital_setup.py`)

Located at the **repository root**. The clinic-facing surface is deliberately a
single file with three dataclass blocks, so a hospital can change LLM vendor or
mailbox without reading any agent code. It is usable two ways: the CLI below, or
`import hospital_setup` from a hospital's own onboarding service.

```
hospital_setup.py                    (repository root)
  LlmSettings    ──environment()──→ AGENT_LLM_PROVIDER / _MODEL / _API_KEY / _BASE_URL
  EmailSettings  ──environment()──→ SMTP_HOST / _PORT / _USERNAME / _PASSWORD,
                                    EMAIL_FROM / EMAIL_FROM_NAME
  AgentSettings  ──environment()──→ AGENT_ESCALATION_EMAIL, policy knobs,
                                    AGENT_LIVE_SENDS, MESSAGING_DRY_RUN
        │
        ├── apply(*, override=True) -> int   writes into os.environ; returns count
        ├── summary()    -> Dict[str, Any]   secret-free (keys/passwords redacted)
        ├── validate()   -> List[str]        pure inspection; [] means good
        └── environment()-> Dict[str, str]   renders without touching anything

        check_llm()        -> (bool, str)  live one-shot call via tools.llm_providers
        check_email()      -> (bool, str)  live SMTP auth via tools.tls SSL context
        send_test_email(r) -> (bool, str)  one real email, bypassing MESSAGING_DRY_RUN
                                           for that call only
```

`LlmSettings.provider` is `anthropic` | `openai` | `azure` | `disabled`; the many
vendor aliases (`ollama`, `vllm`, `litellm`, `deepseek`, `qwen`, ...) all
normalise to `openai`, and an unrecognised name falls back to `anthropic`.
`LlmProviderConfig.kind` is the *normalised* result, which is why the file says
`provider` while the config object says `kind` -- `kind` is never an alias.

Consumption is what makes the file meaningful, and it is asserted by tests:

| Emitted variable | Read by |
|---|---|
| `AGENT_LLM_PROVIDER` / `_MODEL` / `_API_KEY` / `_BASE_URL` | `tools.llm_providers.LlmProviderConfig.from_env` |
| `EMAIL_FROM` / `EMAIL_FROM_NAME` | `tools.config.MessagingConfig`, `SmtpEmailProvider._from_header` |
| `AGENT_MAX_REMINDERS_BEFORE_ESCALATION` / `AGENT_REMINDER_INTERVAL_DAYS` | `core.config.ClinicPolicyConfig.from_env` |

Design rules that matter:

- **`.env` is loaded by the entry points, not by `validate()`.** `main()`,
  `check_llm()`, `check_email()` and `send_test_email()` all call `load_dotenv()`
  first, because the guide tells clinics to *export* `SMTP_PASSWORD` rather than
  paste it into the file. `validate()` stays a pure inspection of whatever is
  already in the environment; calling it directly from library code therefore
  requires loading `.env` first.
- **Blank or garbage policy values fall back to defaults** (`_int()` in
  `core/config.py`), so a typo in a clinic's settings cannot stop the agent.
- **`--send-test` deliberately bypasses `MESSAGING_DRY_RUN`** for that one
  explicit call, without flipping `AGENT.enable_live_sending`. It re-applies the
  override *after* `apply()`, and refuses to report success if the resolved
  config is somehow still simulating — a bug that made it print PASS while
  sending nothing.
- **Two independent switches** (`MESSAGING_DRY_RUN=0` **and**
  `AGENT_LIVE_SENDS=1`) still gate the agent's own sending; `--send-test` is an
  operator action, not the agent acting on its own.

## Module Dependencies

```
orchestrator.py
    ├── core.models (PatientRecord, FollowUpCase, Enums)
    ├── core.config (ClinicPolicyConfig)
    ├── core.data_access (PatientDataStore, CalendarIntegration)
    ├── core.actions (AgentAction)
    ├── agent.business_rules (RecallRuleEngine, UrgencyScorer)
    ├── agent.decision (RuleDecisionEngine, LlmDecisionEngine)
    ├── agent.notifications (Channels, MessageComposer)
    ├── agent.delivery (DeliveryBackend, NotificationOutcome)
    ├── agent.conversation (ConversationManager)
    └── agent.action_handlers (Scheduler, Escalation, Audit)

agent.delivery / agent.decision
    └── tools.messaging, tools.llm_agent    (one-way: agent -> tools)

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

### Relation to the agent layer

The dependency runs one way: `agent/` imports `tools/`, never the reverse.
`tools/` is the *transport* layer -- it knows how to talk to Meta, Twilio,
SMTP, SES and End User Messaging, and it never raises. `agent/` is the
*decision* layer -- it knows which patient to contact, on which channel, and
what to do when that fails.

They meet in exactly one place: `ToolkitDeliveryBackend`
(`agent/delivery.py`) calls the tool registry and translates each `ToolResult`
into a `NotificationOutcome`. That single seam is why the agent's fallback
logic is testable with a scripted backend and no credentials.

`agent/decision.py` is the other consumer of `tools/`: `LlmDecisionEngine`
reuses `tools.llm_agent.create_anthropic_client` and the same tool-use loop,
with `choose_next_action` as its tool.

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

### 7. Strategy Pattern (DECIDE)
**Where:** `agent/decision.py` -- `RuleDecisionEngine` and `LlmDecisionEngine`
**Why:** Action selection is swappable without the orchestrator knowing which
engine ran. The rule engine's `permissible_actions()` is also the guardrail, so
the LLM can only choose from a set that is safe by construction.

### 8. Chain of Responsibility (ACT)
**Where:** `FollowUpAgentOrchestrator._deliver_with_fallback`
**Why:** Each reachable channel gets one attempt in preference order, and the
first success ends the chain. Total failure escalates rather than returning a
false success.

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
        │ MESSAGE_SENT    │◄─┐ an OPEN status: the agent keeps
        └────┬────────────┘  │ working the case, a reminder is
             │               │ not an answer
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

`OPEN_STATUSES` = `{PENDING, MESSAGE_SENT, AWAITING_REPLY}`;
`CLOSED_STATUSES` = `{BOOKED, DECLINED, ESCALATED}`.

**Both** of the two rightmost transitions to `ESCALATED` are forced, not
preferences, and both used to be unreachable:

- *No response + max reminders* needs `reminder_count` to survive between
  cycles. Each cycle rebuilds cases from the data store, so the previous cycle's
  count is carried forward onto the fresh case before deciding
  (`FollowUpAgentOrchestrator._carry_forward`). Without that, the counter resets
  to zero daily and the cap can never be reached.
- *Questions → ESCALATED* includes `ConversationManager.should_escalate`, which
  escalates a critical case after one unanswered reminder -- a check that can
  only fire if `reminder_count` is non-zero when it runs.

A third route exists: if every reachable channel fails, the case is escalated
regardless of the reminder budget (`_escalate_undeliverable`).

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

Tests live in `tests/` at the repository root. The suite needs no credentials
and no network: `FakeTransport` / `FakeSmtpConnection` record requests,
`ScriptedModelClient` replaces the Claude API with a deterministic script, and
`ScriptedBackend` (in `tests/test_agent_delivery.py`) scripts *delivery failures*
so the agent's failure handling is exercised without touching a provider.

```bash
.venv/bin/python -m pytest tests/ -q          # 549 tests
.venv/bin/python -m tools.demo_tool_use       # 3 scenarios, 11 checks
```

`tests/test_agent_delivery.py` is the proof of the agent layer's behaviour. It
drives the real `FollowUpAgentOrchestrator` and asserts on what the agent *did*:

- a failed send falls through to the patient's next channel
- a channel that already failed is not retried
- when every channel fails the case is escalated, never reported as sent
- a patient who never replies still reaches the escalation cap across cycles
- the LLM may only choose actions the rule engine permits, and every fallback
  path (no client, out-of-bounds answer, API error, single option) degrades to
  the rules
- live sending requires both `MESSAGING_DRY_RUN=0` and `AGENT_LIVE_SENDS=1`

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
python web/app.py          # dashboard on http://localhost:8080
# Or
python demo.py             # offline interactive demo
```

### Production (Example with Gunicorn)
```bash
gunicorn -w 4 -b 0.0.0.0:8000 web.app:app
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
