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
```

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
