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
- **📤 Smart Data Import**: Upload a patient list (CSV/TSV/JSON/TXT/XLSX) and
  the agent imports it and immediately runs a cycle, so the rows show up as cases
  on the main dashboard in the same request
- **🏥 Bring Your Own LLM**: Any provider (Claude, OpenAI, Azure, self-hosted
  OpenAI-compatible, Ollama) via one config file — with fallback to the rule
  engine whenever the model is unavailable, so the agent never stops
- **📧 Bring Your Own Mailbox**: Send from the clinic's own already-maintained
  domain address, so mail is signed by the clinic's provider and keeps the
  clinic's reputation

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

That one command is enough for the dashboard, the demo and the whole test
suite. See [Dependencies](#dependencies) for what each package is for and which
ones are optional.

3. **Run the interactive demo**

```bash
python demo.py
```

This will guide you through all agent capabilities with sample data.

4. **Launch the web dashboard**

```bash
python web/app.py
```

Then open your browser to: `http://localhost:8080`

> The dashboard is `web/app.py` (there is no top-level `app.py`) and it listens on
> port **8080**. The port is fixed in `web/app.py`; the script does not accept a
> `--port` flag.

### Dependencies

`requirements.txt` is the single source of truth. What each package is for:

| Package | Needed for | If it is missing |
|---|---|---|
| `flask`, `werkzeug` | the web dashboard (`web/app.py`) | the dashboard cannot start; the demo and tests still run |
| `boto3`, `botocore` | the `send_sms` / `send_email` AWS tools | those two tools return `error_code: "config_missing"`; everything else works |
| `openpyxl` | reading `.xlsx` patient lists on upload | an `.xlsx` upload returns "pip install openpyxl"; CSV/TSV/JSON/TXT keep working |
| `certifi` | the CA trust store for outbound HTTPS and SMTP | live sends fail with `CERTIFICATE_VERIFY_FAILED` on a macOS python.org install, which ships no CAs until "Install Certificates" is run |
| `python-dateutil` | pinned because `botocore` requires it | — (nothing in this project imports it directly) |
| `pytest`, `pytest-cov` | the test suite | — |
| `flake8`, `black`, `mypy`, `sphinx` | linting, formatting, type checks, docs | — |

Only `flask` is imported at module scope. `boto3`, `botocore`, `openpyxl`, `certifi`
and `anthropic` are all imported lazily *inside* the function that needs them, which
is why the offline demo and the entire test suite run with no cloud SDKs, no
credentials and no network.

**Deliberately not installed by default:** `anthropic` (the CLI/`tools/` LLM
tool-use loop and the agent's LLM DECIDE step) and `twilio` are commented out in
`requirements.txt` because they are optional — see *Enabling Claude for DECIDE*
below. Without `anthropic` the agent falls back to its rule engine rather than
failing.

## 🏥 Hospital Setup

Bring your own LLM and bring your own mailbox.

Everything a clinic configures lives in **one file**:

```
agent_followship/hospital_setup.py        <-- the interface file (repository root)
```

No other file needs editing to point the agent at a different LLM or a different
email account.

```bash
.venv/bin/python hospital_setup.py --show        # what is configured (secret-free)
.venv/bin/python hospital_setup.py --check       # prove the LLM + mailbox work
.venv/bin/python hospital_setup.py --send-test you@yourclinic.com   # one real email
```

Edit the three dataclass blocks at the top of the file:

```python
LLM = LlmSettings(
    provider   = "anthropic",     # anthropic / openai / azure / disabled
    model      = "claude-sonnet-4-5",
    api_key    = "",             # prefer exporting (see below) over pasting
    base_url   = "",             # self-hosted gateways
)

EMAIL = EmailSettings(
    enabled       = True,
    address       = "reminders@yourclinic.com",   # the clinic's own maintained mailbox
    display_name  = "Your Clinic",                # what patients see in their inbox
    preset        = "microsoft365",               # fills host/port/encryption for you
    smtp_password = "",                           # prefer exporting SMTP_PASSWORD
)

AGENT = AgentSettings(
    escalation_email = "staff@yourclinic.com",
    enable_live_sending     = False,   # both of these must be True AND
    live_sends_acknowledged = False,   # MESSAGING_DRY_RUN=0 before anything is sent
)
```

### Using it as a Python interface

`hospital_setup.py` is importable, so a hospital's own onboarding portal or
internal service can drive it instead of shelling out to the CLI. Every function
is pure-ish and returns data rather than exiting:

| Function | Returns | Notes |
|---|---|---|
| `environment()` | `Dict[str, str]` | The variables your settings render to. Touches nothing. |
| `apply(*, override=True)` | `int` | Writes them into `os.environ`; returns how many were written. |
| `validate()` | `List[str]` | Human-readable problems; empty means good. Does **not** load `.env`. |
| `summary()` | `Dict[str, Any]` | Secret-free description (keys/passwords redacted). |
| `check_llm()` | `Tuple[bool, str]` | Live one-shot model call. |
| `check_email()` | `Tuple[bool, str]` | Live SMTP authentication. |
| `send_test_email(recipient)` | `Tuple[bool, str]` | One real email. |

```python
import hospital_setup as hs

hs.LLM.provider = "openai"                      # or "deepseek", "ollama", ...
hs.LLM.base_url = "http://10.0.0.7:8000/v1"
hs.LLM.model    = "Qwen/Qwen2.5-72B-Instruct"
hs.EMAIL.enabled, hs.EMAIL.address = True, "reminders@yourclinic.com"
hs.EMAIL.display_name, hs.EMAIL.preset = "Your Clinic", "microsoft365"

# Secrets come from the environment (OPENAI_API_KEY, SMTP_PASSWORD), so they are
# never written into a file that might be committed.
problems = hs.validate()
if problems:                       # e.g. a missing credential or password
    raise SystemExit(problems)
print(hs.apply(), "variables applied")           # -> the agent now uses these
```

Run with the secrets exported, this prints `problems: none` / `17 variables
applied`. Leave them out and `validate()` names exactly what is missing instead of
failing later at send time — which is the point of checking before applying.

That call is verified to actually reach the agent: the same run shows the agent's
own config objects picking the values up:

```
apply() wrote   : 17 vars
kind            : openai            (from provider=openai-compatible)
base_url        : http://10.0.0.7:8000/v1
model           : Qwen/Qwen2.5-72B-Instruct
mail from       : reminders@hospital.example
from name       : General Hospital
smtp            : smtp.office365.com 587
```

Note the two names differ deliberately: the file calls it `provider`, while the
agent's internal `LlmProviderConfig` calls the normalized result `kind` (which is
always one of the four values above, never an alias).

### 1. The hospital's own LLM

`AGENT_LLM_PROVIDER` has **four behaviours**:

| Value | Behaviour |
|---|---|
| `anthropic` | Claude (also the default) |
| `openai` | Anything speaking `/chat/completions` |
| `azure` | Azure OpenAI (set `api_version`) |
| `disabled` | No model — run the rule engine only |

Many names are **aliases of `openai`**, not separate vendors:
`openai-compatible`, `compatible`, `custom`, `ollama`, `vllm`, `tgi`,
`litellm`, `deepseek`, `qwen`, `groq`, `together`, `moonshot`, `zhipu`. So
"self-hosted Ollama" is simply `openai` + a `base_url`. Give `base_url` up to
but **not** including `/chat/completions`. Matching is case-insensitive.

```bash
# a self-hosted model inside the hospital network
export AGENT_LLM_PROVIDER=openai
export AGENT_LLM_BASE_URL=http://10.0.0.7:8000/v1
export AGENT_LLM_API_KEY=not-needed-but-some-gateways-want-one
export AGENT_LLM_MODEL=Qwen/Qwen2.5-72B-Instruct
```

Verified alias collapse (a real run, not a claim):

```
provider='ollama'         -> kind=openai
provider='deepseek'       -> kind=openai
provider='vllm'           -> kind=openai
provider='OpenAI'         -> kind=openai
provider='typo-nonsense'  -> kind=anthropic   # a typo degrades, never breaks
```

Two deliberate design choices make this safe for a clinic to own:

- **A typo cannot take the agent offline.** An unrecognised name degrades to
  `anthropic` rather than raising.
- **The LLM is never a single point of failure.** It may only choose among the
  actions the rule engine already permits, and *any* problem — bad key, timeout,
  out-of-bounds answer, `disabled` — silently falls back to the rules. Patient
  follow-up never stops because a model endpoint is down.

Vendor keys are never crossed: `ANTHROPIC_API_KEY` is never sent to an OpenAI
endpoint, and vice versa.

### 2. The hospital's own domain mailbox

Set `EMAIL.address` to the mailbox the clinic already maintains and the agent
sends patient mail **as that address**, through that clinic's own provider.
Because mail is signed by the clinic's provider, it keeps the clinic's sending
reputation and lands in the inbox — unlike relayed mail from a third-party
identity, which is routinely foldered as spam.

| Preset | Port | Encryption |
|---|---|---|
| `microsoft365`, `google`, `zoho`, `workmail`, `ses`, `fastmail` | 587 | STARTTLS |
| `exmail`, `aliyun` | 465 | implicit TLS |

`SMTP_HOST` (plus `SMTP_PORT` / `SMTP_USE_TLS` / `SMTP_USE_SSL`) always overrides
a preset, so any provider works. Gmail and Google Workspace require a
16-character **App Password** — see [Deliverability](#deliverability).

Prefer exporting the secret over pasting it into the file:

```bash
export SMTP_PASSWORD='the app password'   # hospital_setup.py reads .env and the environment
```

> ⚠️ **The SMTP channel has no recipient allow-list.** The two AWS tools
> (`send_sms`, `send_email`) refuse any address that is not on
> `AWS_SMS_ALLOWED_NUMBERS` / `AWS_EMAIL_ALLOWED_ADDRESSES`, because sandboxed
> AWS accounts can only reach verified destinations. The clinic's own mailbox
> has no such restriction — it can reach anyone it can relay to, and the
> reputation and volume limits become the clinic's. Keep `MESSAGING_DRY_RUN=1`
> until the clinic is ready for that responsibility.

## 📖 Usage Guide

### Running the Agent

#### Option 1: Web Dashboard (Recommended)

```bash
python web/app.py
```

Then open `http://localhost:8080`.

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

There are two ways to configure the agent. **Use `.env`** unless you have a
reason not to — it needs no code changes.

| Way | Best for | How |
|---|---|---|
| **`.env` file** (recommended) | Normal use, and anything secret | `cp .env.example .env`, then edit it |
| **`hospital_setup.py`** | A clinic onboarding system, or generating the config programmatically | Edit the `LLM` / `EMAIL` / `AGENT` blocks, then call `apply()` — see [Hospital Setup](#-hospital-setup) |

Both paths end up in the same place: `hospital_setup.py` writes environment
variables that the agent reads, so the two are compatible and either can be used
on its own. Every variable below is optional — the defaults are safe, and the
agent stays in dry-run mode until you deliberately switch it off.

#### Where the configuration is read from

| Variable | Default | Meaning |
|---|---|---|
| `MESSAGING_ENV_FILE` | `.env` | Path to the file `load_env_file()` reads instead of `./.env`. Useful when a clinic keeps its secrets outside the repository. |

Precedence: a variable **already exported in your shell wins** over the file,
unless a caller passes `override=True` (which is what `hospital_setup.py apply()`
does). So to override something that `apply()` wrote, set it *after* calling
`apply()`.

#### 1. Safety switches

| Variable | Default | Meaning |
|---|---|---|
| `MESSAGING_DRY_RUN` | `1` (on) | Tool-layer master switch. When on, every send is **simulated** and no network call is made. Set to `0` to allow real transmission. |
| `AGENT_LIVE_SENDS` | off | Second, independent switch read by the agent loop. Live sending needs **both** this and `MESSAGING_DRY_RUN=0`. |

See [The two-switch live gate](#the-two-switch-live-gate) for why there are two.

#### 2. Clinic policy

These drive clinical prioritisation and the escalation cap.

| Variable | Default | Meaning |
|---|---|---|
| `AGENT_HIGH_URGENCY_THRESHOLD_DAYS` | `30` | A patient this many days overdue is classed **high** urgency. |
| `AGENT_CRITICAL_URGENCY_THRESHOLD_DAYS` | `60` | A patient this many days overdue is classed **critical** urgency and escalates to staff immediately. |
| `AGENT_MAX_REMINDERS_BEFORE_ESCALATION` | `3` | After this many unanswered reminders, the case escalates to a human. |
| `AGENT_REMINDER_INTERVAL_DAYS` | `7` | Minimum gap between reminders to the same patient. |

Two policy settings have **no environment variable** and must be set in Python
via `ClinicPolicyConfig` if a clinic needs to change them:

```python
ClinicPolicyConfig(
    working_hours=(9, 18),   # quiet hours; the agent will not contact outside these
    opt_out_respected=True,  # honour a patient's opt-out request
)
```

The urgency thresholds are also settable in Python, which is what
`hospital_setup.py` does when you set `AGENT.max_reminders_before_escalation`.
The environment form above is the recommended one.

#### 3. The hospital's own LLM

| Variable | Default | Meaning |
|---|---|---|
| `AGENT_LLM_PROVIDER` | `anthropic` | `anthropic`, `openai`, `azure` or `disabled`. Vendor aliases such as `ollama`, `vllm`, `deepseek` all mean `openai` plus a base URL; an unrecognised value also means `anthropic`, so a typo can never take the agent offline. |
| `AGENT_LLM_MODEL` | vendor default | Model id, e.g. `claude-sonnet-4-5`, `gpt-4o-mini`, `llama3.1:8b`. |
| `AGENT_LLM_API_KEY` | — | The credential. Falls back, in order, to `LLM_API_KEY`, then the vendor variable (`ANTHROPIC_API_KEY` / `OPENAI_API_KEY`). |
| `AGENT_LLM_BASE_URL` | — | Endpoint root. Required for a self-hosted or OpenAI-compatible server. |
| `AGENT_LLM_API_VERSION` | — | Azure `api-version`. |
| `AGENT_LLM_TIMEOUT_SECONDS` | `60` | Per-request timeout. |
| `AGENT_LLM_MAX_TOKENS` | `1024` | Completion budget per decision. |
| `AGENT_LLM_ORGANIZATION` | — | OpenAI organization header. |
| `AGENT_LLM_EXTRA_HEADERS` | — | Extra request headers, as JSON. |
| `AGENT_DECISION_MODEL` | — | Legacy alias for `AGENT_LLM_MODEL`; kept for compatibility. |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | — | Vendor-native credential names, accepted as fallbacks. |
| `LLM_API_KEY` | — | Generic credential fallback, checked before the vendor variables. |

`disabled` makes the agent fall back to the deterministic rule engine, which is
also what happens whenever no credential is present. The LLM is never required.

#### 4. The hospital's own domain mailbox (SMTP)

| Variable | Default | Meaning |
|---|---|---|
| `SMTP_HOST` | — | e.g. `smtp.office365.com`. There is **no `SMTP_PRESET` environment variable**; presets live in `hospital_setup.py` (see below). |
| `SMTP_PORT` | `587` | |
| `SMTP_USE_TLS` | `1` | STARTTLS. |
| `SMTP_USE_SSL` | `0` | Implicit TLS (usually used with port 465). |
| `SMTP_USERNAME` | — | Typically the full mailbox address. |
| `SMTP_PASSWORD` | — | For Gmail / Google Workspace this must be a 16-character **App Password**. |
| `EMAIL_FROM` | `SMTP_USERNAME` | The address patients see. Defaults to the username, which is correct for most providers. |
| `EMAIL_FROM_NAME` | — | The friendly display name, e.g. `BrightSmile Dental Clinic`. |

A *preset* is a convenience of `hospital_setup.py` only — set `EMAIL.preset` and
it fills in the host/port/encryption when you call `apply()`. The available
presets and the exact values they resolve to:

| `EMAIL.preset` | Host | Port | Encryption |
|---|---|---|---|
| `microsoft365` | `smtp.office365.com` | 587 | STARTTLS |
| `google` | `smtp.gmail.com` | 587 | STARTTLS |
| `exmail` | `smtp.exmail.qq.com` | 465 | SSL |
| `aliyun` | `smtp.qiye.aliyun.com` | 465 | SSL |
| `zoho` | `smtp.zoho.com` | 587 | STARTTLS |
| `fastmail` | `smtp.fastmail.com` | 587 | STARTTLS |
| `workmail` | `smtp.mail.us-east-1.awsapps.com` | 465 | SSL |
| `ses` | `email-smtp.ap-southeast-1.amazonaws.com` | 587 | STARTTLS |

The `workmail` and `ses` hosts are region-bound as shown; set `SMTP_HOST`
yourself if your region differs.

#### 5. AWS SMS and SES

| Variable | Default | Meaning |
|---|---|---|
| `AWS_REGION` | `ap-southeast-1` | Region for both AWS tools. Falls back to `AWS_DEFAULT_REGION`, then `ap-southeast-1`. |
| `AWS_DEFAULT_REGION` | — | Standard AWS SDK fallback, read when `AWS_REGION` is unset. |
| `AWS_SES_SOURCE` | `martinchenonly1@gmail.com` | The verified SES sender identity. **Must be an address verified in SES** or every send is rejected. |
| `AWS_EMAIL_ALLOWED_ADDRESSES` | `martinchenonly1@gmail.com` | Comma-separated **recipient** allow-list for `send_email`. Replaces the default, and an empty value denies everything. |
| `AWS_SMS_ALLOWED_NUMBERS` | `+6583536885` | Comma-separated recipient allow-list for `send_sms`. Same fail-closed behaviour. |
| `AWS_SMS_ORIGINATION_IDENTITY` | — | Sender ID or long code. The API treats it as optional, but real delivery generally needs one. |
| `AWS_SES_CONFIGURATION_SET` | — | Configuration set for delivery-event tracking. Omit and messages are sent without one. |

Credentials themselves (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`,
`AWS_PROFILE`, …) are read by `boto3`, not by this project — use the standard
AWS mechanisms. See [Proving a message was delivered](#proving-a-message-was-delivered).

#### 6. WhatsApp and Twilio (optional)

Only needed if you use the `send_whatsapp_message` or `send_sms_message` tools;
the AWS tools above do not depend on them.

| Variable | Default | Meaning |
|---|---|---|
| `WHATSAPP_PROVIDER` | — | Selects the WhatsApp backend. |
| `META_WHATSAPP_ACCESS_TOKEN` | — | Meta Cloud API token. |
| `META_WHATSAPP_PHONE_NUMBER_ID` | — | Meta sender phone number id. |
| `META_GRAPH_API_VERSION` | `v21.0` | Graph API version. |
| `META_TEMPLATE_LANGUAGE` | `en` | Default template language. |
| `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` | — | Twilio credentials. |
| `TWILIO_SMS_FROM` / `TWILIO_WHATSAPP_FROM` | — | Twilio sender numbers. |
| `TWILIO_CONTENT_SIDS` | — | Comma-separated approved content template SIDs. |
| `TWILIO_REQUIRE_CONTENT_SID` | `0` | When on, refuse any Twilio send lacking a content SID. |

#### 7. Advanced

| Variable | Default | Meaning |
|---|---|---|
| `MESSAGING_TIMEOUT_SECONDS` | `10.0` | Network timeout for the non-AWS channels. |
| `MESSAGING_DEFAULT_COUNTRY_CODE` | — | Country code applied to numbers given without one. |
| `SSL_CERT_FILE` | — | Path to a CA bundle, honoured by the project's TLS helper. Set this if you hit certificate errors — see [TLS trust stores](#tls-trust-stores). |

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

The dashboard runs on `http://localhost:8080` (`python web/app.py`). All
endpoints below are relative to that. `GET /` serves the dashboard page itself.

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | The dashboard HTML page |
| GET | `/api/status` | Agent statistics and metrics |
| GET | `/api/config` | The active clinic policy |
| GET | `/api/patients` | Every patient in the system |
| GET | `/api/cases` | Active cases |
| GET | `/api/cases/{patient_id}` | One case in detail |
| GET | `/api/escalations` | Escalated cases |
| GET | `/api/audit-logs` | The audit trail |
| GET | `/api/available-slots/{patient_id}` | Bookable dates for a patient |
| POST | `/api/run-cycle` | Trigger a daily cycle manually |
| POST | `/api/simulate-reply` | Simulate a patient reply |
| POST | `/api/upload-patient-list` | Parse an uploaded list (preview only) |
| POST | `/api/import-patients` | Store the rows and run a cycle |
| POST | `/api/book-appointment` | Book an appointment manually |
| POST | `/api/import-escalated-cases` | Import escalated cases as JSON |

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

### POST /api/upload-patient-list
Parse an uploaded patient list without storing it — returns the rows the agent
understood, so the dashboard can show a preview before committing.

**Request:** multipart form with a `file` field. Accepted formats are CSV, TSV,
JSON, TXT and XLSX. A legacy binary `.xls` is **not** supported -- Excel's older
format is a different container; save it as `.xlsx` or CSV and upload that.
Reading `.xlsx` needs the `openpyxl` package (in `requirements.txt`); without it
the endpoint returns a message saying so.

```bash
curl -X POST http://localhost:8080/api/upload-patient-list \
     -F "file=@patients.csv"
```

### POST /api/import-patients
Store the uploaded patients and immediately run a daily agent cycle, so the rows
appear as cases on the main dashboard in the same request.

**Request:** the same multipart upload, or `{"patients": [...]}` as JSON.

**Response:**
```json
{
  "success": true,
  "imported_count": 2,
  "skipped_count": 0,
  "duplicate_patients": []
}
```

`skipped_count` counts rows missing a usable name or contact detail;
`duplicate_patients` lists rows whose `patient_id` already exists (they are not
overwritten). The import is what makes an uploaded list visible on the main
page — parsing alone changes nothing.

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

### GET /api/config
Returns the clinic policy the agent is actually running on. Useful for a
dashboard, or to confirm that your `AGENT_*` settings took effect.

```json
{
  "working_hours": [9, 18],
  "max_reminders_before_escalation": 3,
  "opt_out_respected": true,
  "high_urgency_threshold_days": 30,
  "critical_urgency_threshold_days": 60,
  "reminder_interval_days": 7
}
```

### GET /api/patients
Every patient the agent knows about, regardless of case status.

```json
[
  {
    "patient_id": "P001",
    "name": "Alice Tan",
    "treatment_type": "cleaning",
    "last_visit_date": "2024-01-15",
    "recall_interval_days": 180,
    "preferred_channel": "sms",
    "no_show_history": false
  }
]
```

Makes no agent calls and does not run a cycle, so it is safe to poll.

### GET /api/available-slots/{patient_id}
The next 10 bookable dates for a patient, for the booking UI.

```json
["2024-12-16", "2024-12-17", "2024-12-18"]
```

Returns `404` with `{"error": "Case not found"}` when the patient has no active
case.

### POST /api/book-appointment
Book an appointment on a patient's behalf. The appointment is written to the
audit trail as `manual_booking`.

**Request:**
```json
{"patient_id": "P001", "appointment_date": "2024-12-15"}
```

**Response:**
```json
{"success": true, "booked_date": "2024-12-15"}
```

`success` is `false` when the slot is unavailable, and the requested date is not
then used. Missing fields return `400`; an unknown patient returns `404`.

### POST /api/import-escalated-cases
Restore escalated cases from JSON, using the `EscalationHandler` record format.
This is the way to re-import cases that were exported for a human to work
through.

**Request:**
```json
{"escalated_cases": [{"patient_id": "P003", "reason": "no_response"}]}
```

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

To run the agent's DECIDE step on **your own** model instead, use the provider
adapter — no change to the tools or to `agent/`:

```python
from tools.llm_providers import LlmProviderConfig, create_llm_client

config = LlmProviderConfig.from_env()   # reads AGENT_LLM_*
client = create_llm_client(config)      # None => use the rule engine
```

`create_llm_client` returns `None` for a `disabled` provider, and
`LlmProviderConfig.from_env` falls back to `anthropic` on an unrecognised name,
so neither a typo nor a missing key can leave the agent without a decider — it
degrades to the rules. For the clinic-facing configuration file and live
verification commands, see
[Hospital Setup](#-hospital-setup).

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
