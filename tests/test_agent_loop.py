"""
End-to-end behaviour of the tool-use loop.

The scenario under test is the one that motivated the layer: WhatsApp rejects
the send because the recipient is not verified. The agent must observe that,
pick another channel and deliver -- or escalate -- without the process dying.
"""

from __future__ import annotations
import json
from typing import Any, Dict, List

from conftest import EMAIL, PHONE, make_registry
from tools.errors import SendErrorCode
from tools.llm_agent import (
    AgentRun,
    ScriptedModelClient,
    ToolUseAgent,
    last_tool_result_payload,
    normalize_turn,
)
from tools.messaging import EscalationLog
from tools.schemas import get_tool_schemas, tool_names, validate_schema_coverage
from tools.transport import FakeSmtpConnection, FakeTransport

META_NOT_VERIFIED = {
    "status_code": 400,
    "body": {
        "error": {
            "message": "(#131030) Recipient phone number not in allowed list",
            "code": 131030,
            "type": "OAuthException",
        }
    },
}
TWILIO_SENT = {"status_code": 201, "body": {"sid": "SM9f2a1c7d", "status": "queued"}}
TWILIO_OPTED_OUT = {
    "status_code": 400,
    "body": {"code": 21610, "message": "Attempt to send to unsubscribed recipient"},
}
TWILIO_RATE_LIMITED = {
    "status_code": 429,
    "body": {"code": 20429, "message": "Too many requests"},
}


def tool_use(name: str, arguments: Dict[str, Any], call_id: str = "t1") -> Dict[str, Any]:
    """Build one scripted assistant turn that requests a tool."""
    return {
        "stop_reason": "tool_use",
        "content": [
            {"type": "tool_use", "id": call_id, "name": name, "input": arguments}
        ],
    }


def final(text: str) -> Dict[str, Any]:
    """Build a scripted final answer."""
    return {"stop_reason": "end_turn", "content": [{"type": "text", "text": text}]}


WHATSAPP_ARGS = {
    "recipient": PHONE,
    "template": "appointment_reminder",
    "params": ["Alex", "cleaning", "+15550002222"],
}
SMS_ARGS = {"recipient": PHONE, "body": "Your follow-up is due, please call us."}
EMAIL_ARGS = {
    "recipient": EMAIL,
    "subject": "Your follow-up",
    "body": "Please call the clinic.",
}


class TestTurnNormalization:
    """Raw provider payloads -> internal turn objects."""

    def test_text_only_turn(self) -> None:
        turn = normalize_turn([{"type": "text", "text": "hello"}])
        assert turn.text == "hello"
        assert turn.tool_calls == []

    def test_tool_use_turn(self) -> None:
        turn = normalize_turn(
            [
                {"type": "text", "text": "trying whatsapp"},
                {
                    "type": "tool_use",
                    "id": "abc",
                    "name": "send_whatsapp_message",
                    "input": {"recipient": PHONE},
                },
            ],
            stop_reason="tool_use",
        )
        assert turn.text == "trying whatsapp"
        assert len(turn.tool_calls) == 1
        assert turn.tool_calls[0].name == "send_whatsapp_message"
        assert turn.tool_calls[0].arguments == {"recipient": PHONE}
        assert turn.stop_reason == "tool_use"

    def test_empty_content_is_safe(self) -> None:
        turn = normalize_turn(None)
        assert turn.text == ""
        assert turn.tool_calls == []


class TestToolSchemas:
    """The schemas are the model's only instructions: they must be complete."""

    def test_every_tool_has_a_schema(self) -> None:
        validate_schema_coverage()

    def test_schemas_teach_the_failure_protocol(self) -> None:
        schemas = {schema["name"]: schema for schema in get_tool_schemas()}
        for name in (
            "send_whatsapp_message",
            "send_sms_message",
            "send_email_message",
            "send_sms",
            "send_email",
        ):
            description = schemas[name]["description"]
            assert "never raises" in description
            assert "error_code" in description
            assert "suggested_fallback_channels" in description
            assert "escalate_to_staff" in description

    def test_info_tools_announce_their_status_value(self) -> None:
        schemas = {schema["name"]: schema for schema in get_tool_schemas()}
        for name in ("get_candidate_send_channels", "list_message_templates"):
            assert "'ok'" in schemas[name]["description"]

    def test_escalation_tool_announces_its_status_value(self) -> None:
        schemas = {schema["name"]: schema for schema in get_tool_schemas()}
        assert "'escalated'" in schemas["escalate_to_staff"]["description"]

    def test_send_schemas_require_a_recipient(self) -> None:
        schemas = {schema["name"]: schema for schema in get_tool_schemas()}
        for name in tool_names():
            schema = schemas[name]
            assert schema["input_schema"]["type"] == "object"
        assert "recipient" in schemas["send_sms_message"]["input_schema"]["required"]
        # The AWS tools name the recipient differently but still require it.
        assert "phone_number" in schemas["send_sms"]["input_schema"]["required"]
        assert "to_email" in schemas["send_email"]["input_schema"]["required"]

    def test_schema_subset_selection(self) -> None:
        schemas = get_tool_schemas(["send_sms_message", "escalate_to_staff"])
        assert [schema["name"] for schema in schemas] == [
            "send_sms_message",
            "escalate_to_staff",
        ]

    def test_schemas_are_json_serializable(self) -> None:
        json.dumps(get_tool_schemas())


class TestScriptedModelClient:
    """The double that makes offline tool-use testing possible."""

    def test_mappings_are_consumed_once(self) -> None:
        client = ScriptedModelClient([final("done")])
        first = client.messages.create(messages=[])
        second = client.messages.create(messages=[])
        assert first["content"][0]["text"] == "done"
        assert second == {"stop_reason": "end_turn", "content": []}

    def test_callables_persist_across_turns(self) -> None:
        seen: List[int] = []

        def policy(messages: List[Dict[str, Any]]) -> Dict[str, Any]:
            seen.append(len(messages))
            return final("again")

        client = ScriptedModelClient([policy])
        client.messages.create(messages=[{"role": "user", "content": "a"}])
        client.messages.create(messages=[{"role": "user", "content": "b"}])
        assert len(seen) == 2

    def test_requests_are_recorded_with_the_prompt(self) -> None:
        client = ScriptedModelClient([final("hi")])
        client.messages.create(model="m", messages=[{"role": "user", "content": "x"}])
        assert client.requests[0]["model"] == "m"


class TestAgentRun:
    """The loop must terminate and hand back everything that happened."""

    def test_no_tool_call_is_a_single_turn(self, env: dict) -> None:
        registry = make_registry(env)
        agent = ToolUseAgent(registry, client=ScriptedModelClient([final("nothing to do")]))
        run = agent.run("All patients are up to date.")
        assert run.text == "nothing to do"
        assert run.iterations == 1
        assert run.tool_results == []
        assert run.hit_iteration_cap is False

    def test_iteration_cap_stops_a_tool_using_loop(self, env: dict) -> None:
        """A model that never stops calling tools must not hang the program."""

        def forever(messages: List[Dict[str, Any]]) -> Dict[str, Any]:
            return tool_use("send_sms_message", SMS_ARGS)

        registry = make_registry(env, transport=FakeTransport())
        agent = ToolUseAgent(
            registry, client=ScriptedModelClient([forever]), max_iterations=3
        )
        run = agent.run("spam forever")
        assert run.iterations == 3
        assert run.hit_iteration_cap is True
        assert len(run.tool_results) == 3
        assert "limit" in run.text

    def test_unknown_tool_from_the_model_is_recoverable(self, env: dict) -> None:
        registry = make_registry(env)
        agent = ToolUseAgent(
            registry,
            client=ScriptedModelClient(
                [
                    tool_use("send_carrier_pigeon", {"recipient": PHONE}, "c1"),
                    final("gave up on pigeons"),
                ]
            ),
        )
        run = agent.run("deliver this")
        assert run.tool_results[0].error_code is SendErrorCode.UNKNOWN
        assert run.text == "gave up on pigeons"

    def test_multi_tool_turn_runs_every_call(self, env: dict) -> None:
        registry = make_registry(env, transport=FakeTransport([TWILIO_SENT]))
        turn = {
            "stop_reason": "tool_use",
            "content": [
                {
                    "type": "tool_use",
                    "id": "c1",
                    "name": "send_sms_message",
                    "input": SMS_ARGS,
                },
                {
                    "type": "tool_use",
                    "id": "c2",
                    "name": "list_message_templates",
                    "input": {},
                },
            ],
        }
        agent = ToolUseAgent(
            registry, client=ScriptedModelClient([turn, final("sent")])
        )
        run = agent.run("send and tell me the templates")
        assert [result.tool for result in run.tool_results] == [
            "send_sms_message",
            "list_message_templates",
        ]


class TestFailureIsPerceivable:
    """The requirement: a rejection is information the model can act on."""

    def test_not_verified_whatsapp_becomes_a_channel_switch(self, env: dict) -> None:
        transport = FakeTransport([META_NOT_VERIFIED, TWILIO_SENT])
        registry = make_registry(env, transport=transport)
        state: Dict[str, Any] = {"attempted": [], "asked": False}

        def policy(messages: List[Dict[str, Any]]) -> Dict[str, Any]:
            payload = last_tool_result_payload(messages)

            if payload is None:
                state["attempted"].append("whatsapp")
                return tool_use("send_whatsapp_message", WHATSAPP_ARGS, "t1")

            if payload["status"] == "sent":
                return final(
                    f"delivered via {payload['channel']} "
                    f"({payload.get('message_id')})"
                )

            if payload["status"] == "ok":
                options = [
                    channel
                    for channel in payload["data"]["fallback_order"]
                    if channel not in state["attempted"]
                ]
                state["attempted"].append(options[0])
                return tool_use("send_sms_message", SMS_ARGS, "t3")

            if not state["asked"]:
                state["asked"] = True
                return tool_use(
                    "get_candidate_send_channels",
                    {
                        "failed_channel": payload["channel"],
                        "error_code": payload["error_code"],
                    },
                    "t2",
                )
            return tool_use("escalate_to_staff", {
                "case_id": "C1",
                "patient_id": "P1",
                "reason": "no channel left",
            })

        agent = ToolUseAgent(registry, client=ScriptedModelClient([policy]))
        run = agent.run("Remind patient P1 about their appointment (prefers WhatsApp).")

        assert run.tool_results[0].error_code is SendErrorCode.RECIPIENT_NOT_VERIFIED
        assert "whatsapp" not in run.tool_results[0].suggested_fallback_channels
        delivered = run.delivered_result()
        assert delivered is not None
        assert delivered.channel == "sms"
        assert run.escalated is False
        # WhatsApp request, then the SMS retry -- nothing blindly repeated.
        assert len(transport.requests) == 2

    def test_failed_delivery_reaches_the_model_as_a_tool_result(
        self, env: dict
    ) -> None:
        """The model sees the failure inside the transcript, not as a crash."""
        transport = FakeTransport([META_NOT_VERIFIED])
        registry = make_registry(env, transport=transport)
        agent = ToolUseAgent(
            registry,
            client=ScriptedModelClient(
                [tool_use("send_whatsapp_message", WHATSAPP_ARGS, "t1"), final("ok")]
            ),
        )
        run = agent.run("Send the reminder.")
        result_block = run.transcript[-2]["content"][0]
        assert result_block["type"] == "tool_result"
        assert result_block["is_error"] is True
        payload = json.loads(result_block["content"])
        assert payload["status"] == "failed"
        assert payload["error_code"] == "recipient_not_verified"
        assert payload["provider_code"] == "131030"
        assert payload["suggested_fallback_channels"] == ["sms", "email"]

    def test_dead_end_escalates_with_evidence(self, env: dict) -> None:
        escalation_log = EscalationLog()
        transport = FakeTransport([META_NOT_VERIFIED, TWILIO_OPTED_OUT])
        registry = make_registry(
            env, transport=transport, escalation_log=escalation_log
        )

        def policy(messages: List[Dict[str, Any]]) -> Dict[str, Any]:
            payload = last_tool_result_payload(messages)
            if payload is None:
                return tool_use("send_whatsapp_message", WHATSAPP_ARGS, "t1")
            if payload["status"] == "sent":
                return final("delivered")
            if payload["status"] == "escalated":
                return final(f"escalated as {payload['data']['escalation_id']}")
            if payload["channel"] == "whatsapp":
                return tool_use("send_sms_message", SMS_ARGS, "t2")
            return tool_use(
                "escalate_to_staff",
                {
                    "case_id": "CASE-1",
                    "patient_id": "P1",
                    "reason": f"every channel failed ({payload['error_code']})",
                    "details": {"last_error_code": payload["error_code"]},
                    "urgency": "high",
                },
                "t3",
            )

        agent = ToolUseAgent(registry, client=ScriptedModelClient([policy]))
        run = agent.run("Reach patient P1 however you can.")

        assert run.escalated is True
        assert run.delivered_result() is None
        assert len(escalation_log.records) == 1
        record = escalation_log.records[0]
        assert record.details["last_error_code"] == "opted_out"
        assert record.urgency == "high"
        assert run.text.startswith("escalated as ESC-")

    def test_retryable_failure_offers_no_channel_but_invites_a_retry(
        self, env: dict
    ) -> None:
        smtp = FakeSmtpConnection()
        transport = FakeTransport([META_NOT_VERIFIED, TWILIO_RATE_LIMITED])
        registry = make_registry(env, transport=transport, smtp=smtp)

        def policy(messages: List[Dict[str, Any]]) -> Dict[str, Any]:
            payload = last_tool_result_payload(messages)
            if payload is None:
                return tool_use("send_whatsapp_message", WHATSAPP_ARGS, "t1")
            if payload["status"] == "sent":
                return final("delivered")
            if payload["retryable"] and not payload["suggested_fallback_channels"]:
                return tool_use("send_email_message", EMAIL_ARGS, "t3")
            return tool_use("send_sms_message", SMS_ARGS, "t2")

        agent = ToolUseAgent(registry, client=ScriptedModelClient([policy]))
        run = agent.run("Remind the patient.")

        rate_limited = [
            r for r in run.failures if r.error_code is SendErrorCode.RATE_LIMITED
        ]
        assert rate_limited and rate_limited[0].retryable is True
        assert rate_limited[0].suggested_fallback_channels == []
        assert run.delivered_result() is not None
        assert run.delivered_result().channel == "email"
        assert len(smtp.sent) == 1

    def test_email_delivery_after_both_text_channels_fail(self, env: dict) -> None:
        smtp = FakeSmtpConnection()
        transport = FakeTransport([META_NOT_VERIFIED, TWILIO_OPTED_OUT])
        registry = make_registry(env, transport=transport, smtp=smtp)

        def policy(messages: List[Dict[str, Any]]) -> Dict[str, Any]:
            payload = last_tool_result_payload(messages)
            if payload is None:
                return tool_use("send_whatsapp_message", WHATSAPP_ARGS, "t1")
            if payload["status"] == "sent":
                return final("delivered by email")
            if payload["channel"] == "whatsapp":
                return tool_use("send_sms_message", SMS_ARGS, "t2")
            return tool_use("send_email_message", EMAIL_ARGS, "t3")

        agent = ToolUseAgent(registry, client=ScriptedModelClient([policy]))
        run = agent.run("Remind the patient.")
        delivered = run.delivered_result()
        assert delivered is not None
        assert delivered.channel == "email"
        assert len(smtp.sent) == 1


class TestDecisionSupportTools:
    """The tools that let the model reason about channels and templates."""

    def test_candidates_exclude_the_failed_channel(self, env: dict) -> None:
        registry = make_registry(env)
        result = registry.call(
            "get_candidate_send_channels",
            {"failed_channel": "whatsapp", "error_code": "recipient_not_verified"},
        )
        assert result.status == "ok"
        assert "whatsapp" not in result.data["fallback_order"]
        assert set(result.data["fallback_order"]) == {"sms", "email"}
        assert result.data["channels"]["whatsapp"]["just_failed"] is True

    def test_preferred_channel_is_honoured(self, env: dict) -> None:
        registry = make_registry(env)
        result = registry.call(
            "get_candidate_send_channels", {"preferred_channel": "email"}
        )
        assert result.data["fallback_order"][0] == "email"
        assert result.data["channels"]["email"]["preferred"] is True

    def test_unconfigured_channels_are_flagged(self) -> None:
        registry = make_registry({})
        result = registry.call("get_candidate_send_channels", {})
        assert all(
            info["configured"] is False for info in result.data["channels"].values()
        )
        assert "skipping" in result.data["guidance"]

    def test_template_catalogue(self, env: dict) -> None:
        registry = make_registry(env)
        result = registry.call("list_message_templates", {})
        assert result.status == "ok"
        names = {entry["name"] for entry in result.data["templates"]}
        assert "appointment_reminder" in names
        reminder = next(
            entry
            for entry in result.data["templates"]
            if entry["name"] == "appointment_reminder"
        )
        assert reminder["param_count"] == 3
        assert result.data["count"] == len(result.data["templates"])


class TestEscalationReceipts:
    """Escalations are durable enough for a human to pick up."""

    def test_escalation_ids_are_unique_and_ordered(self, env: dict) -> None:
        log = EscalationLog()
        registry = make_registry(env, escalation_log=log)
        first = registry.call(
            "escalate_to_staff",
            {"case_id": "C1", "patient_id": "P1", "reason": "no channel"},
        )
        second = registry.call(
            "escalate_to_staff",
            {"case_id": "C2", "patient_id": "P2", "reason": "no channel"},
        )
        assert first.data["escalation_id"].endswith("-0001")
        assert second.data["escalation_id"].endswith("-0002")
        assert first.status == "escalated"

    def test_escalation_is_persisted_to_disk(self, env: dict, tmp_path: Any) -> None:
        target = tmp_path / "escalations.jsonl"
        log = EscalationLog(str(target))
        registry = make_registry(env, escalation_log=log)
        registry.call(
            "escalate_to_staff",
            {
                "case_id": "C1",
                "patient_id": "P1",
                "reason": "no channel",
                "urgency": "high",
            },
        )
        lines = target.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["case_id"] == "C1"
        assert record["urgency"] == "high"

    def test_invalid_urgency_is_normalized(self, env: dict) -> None:
        registry = make_registry(env)
        result = registry.call(
            "escalate_to_staff",
            {
                "case_id": "C1",
                "patient_id": "P1",
                "reason": "no channel",
                "urgency": "URGENT!!",
            },
        )
        assert result.data["urgency"] == "normal"


class TestAuditTrail:
    """Every attempt is recorded, whatever its outcome."""

    def test_send_attempts_are_recorded_once_each(self, env: dict) -> None:
        transport = FakeTransport([META_NOT_VERIFIED, TWILIO_SENT])
        registry = make_registry(env, transport=transport)
        registry.call("send_whatsapp_message", WHATSAPP_ARGS)
        registry.call("send_sms_message", SMS_ARGS)
        history = registry.history
        assert len(history) == 2
        assert history[0]["status"] == "failed"
        assert history[0]["error_code"] == "recipient_not_verified"
        assert history[1]["status"] == "sent"
        assert history[1]["simulated"] is False
        json.dumps(history)

    def test_decision_support_calls_are_not_send_attempts(self, env: dict) -> None:
        """Only things that left the building belong in the audit trail."""
        registry = make_registry(env)
        registry.call("list_message_templates", {})
        registry.call("get_candidate_send_channels", {})
        assert registry.history == []

    def test_escalations_are_audited(self, env: dict) -> None:
        registry = make_registry(env)
        registry.call(
            "escalate_to_staff",
            {"case_id": "C1", "patient_id": "P1", "reason": "no channel"},
        )
        assert len(registry.history) == 1
        assert registry.history[0]["status"] == "escalated"
        assert registry.history[0]["channel"] == "staff"

    def test_audit_entries_never_leak_secrets(self, env: dict) -> None:
        transport = FakeTransport([META_NOT_VERIFIED])
        registry = make_registry(env, transport=transport)
        registry.call("send_whatsapp_message", WHATSAPP_ARGS)
        serialized = json.dumps(registry.history)
        assert "test-token" not in serialized
        assert "test-auth-token" not in serialized


class TestAgentRunAggregates:
    """Convenience accessors used by callers and tests."""

    def _run(self, results: List[Any], text: str = "done") -> AgentRun:
        return AgentRun(
            text=text, tool_results=results, transcript=[], iterations=1
        )

    def _result(self, **kwargs: Any) -> Any:
        from tools.result import ToolResult

        defaults: Dict[str, Any] = {
            "tool": "send_sms_message",
            "success": True,
            "channel": "sms",
            "recipient": PHONE,
            "message_id": "SM1",
        }
        defaults.update(kwargs)
        return ToolResult(**defaults)

    def test_sent_and_failures_split(self) -> None:
        run = self._run(
            [
                self._result(success=False, error_code=SendErrorCode.OPTED_OUT),
                self._result(),
            ]
        )
        assert len(run.sent) == 1
        assert len(run.failures) == 1
        assert run.delivered_result() is not None

    def test_simulated_send_is_not_a_delivery(self) -> None:
        run = self._run([self._result(simulated=True)])
        assert run.delivered_result() is None

    def test_escalation_is_not_a_delivery(self) -> None:
        run = self._run(
            [
                self._result(
                    tool="escalate_to_staff",
                    channel="staff",
                    kind="handoff",
                    message_id=None,
                    data={"escalation_id": "ESC-1"},
                )
            ]
        )
        assert run.escalated is True
        assert run.delivered_result() is None

    def test_no_results(self) -> None:
        run = self._run([])
        assert run.sent == []
        assert run.failures == []
        assert run.escalated is False
        assert run.delivered_result() is None


class TestPackageFacade:
    """The lazy facade keeps import cycles away from callers."""

    def test_public_exports(self) -> None:
        import tools

        assert tools.__version__
        assert "send_whatsapp_message" in tools.TOOL_NAMES
        registry = tools.build_tool_registry()
        assert registry.call("list_message_templates", {}).status == "ok"
        assert tools.get_tool_schemas() is not None

    def test_registry_exposes_its_tools(self, env: dict) -> None:
        registry = make_registry(env)
        assert set(registry.names) == set(tool_names())
        assert "recipient" in registry.parameters_for("send_sms_message")
