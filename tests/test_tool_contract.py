"""
The tool contract: every call returns a ``ToolResult`` and never raises.

This is the requirement the whole layer exists for -- when WhatsApp answers
"recipient not verified", the *model* must be able to see that and decide what
to do next, instead of the process dying.
"""

from __future__ import annotations
import inspect
import json
from typing import Any, Dict

import pytest

from conftest import EMAIL, PHONE, make_registry
from tools.errors import SendErrorCode
from tools.messaging import TOOL_NAMES, normalize_email, normalize_phone
from tools.result import ToolResult
from tools.transport import FakeSmtpConnection, FakeTransport


class TestToolResultInvariants:
    """Derived metadata on the single return type."""

    def test_failure_derives_retryable_and_fallbacks(self) -> None:
        result = ToolResult(
            tool="send_whatsapp_message",
            success=False,
            channel="whatsapp",
            recipient=PHONE,
            error_code=SendErrorCode.RECIPIENT_NOT_VERIFIED,
            error_message="not in allowed list",
            provider_code="131030",
        )
        assert result.is_error is True
        assert result.retryable is False
        assert result.suggested_fallback_channels == ["sms", "email"]
        assert result.hint

    def test_failed_channel_is_never_suggested_again(self) -> None:
        """Retrying the channel that just failed is exactly the wrong move."""
        result = ToolResult(
            tool="send_sms_message",
            success=False,
            channel="sms",
            recipient=PHONE,
            error_code=SendErrorCode.RECIPIENT_NOT_VERIFIED,
        )
        assert result.suggested_fallback_channels == ["email"]

    def test_success_carries_no_failure_metadata(self) -> None:
        result = ToolResult(
            tool="send_sms_message",
            success=True,
            channel="sms",
            recipient=PHONE,
            message_id="SM123",
            error_code=SendErrorCode.RATE_LIMITED,
            error_message="stale",
        )
        assert result.is_error is False
        assert result.error_code is None
        assert result.error_message is None
        assert result.retryable is False
        assert result.suggested_fallback_channels == []

    @pytest.mark.parametrize(
        "kind,success,expected",
        [
            ("send", True, "sent"),
            ("info", True, "ok"),
            ("handoff", True, "escalated"),
            ("send", False, "failed"),
            ("info", False, "failed"),
        ],
    )
    def test_status_wording(self, kind: str, success: bool, expected: str) -> None:
        result = ToolResult(
            tool="t", success=success, channel="c", recipient="r", kind=kind
        )
        assert result.status == expected

    def test_payload_is_json_serializable_and_drops_nulls(self) -> None:
        result = ToolResult(
            tool="send_email_message",
            success=True,
            channel="email",
            recipient=EMAIL,
            message_id="<smtp-1>",
        )
        payload = result.to_payload()
        assert payload == {
            "status": "sent",
            "channel": "email",
            "recipient": EMAIL,
            "message_id": "<smtp-1>",
        }
        assert json.loads(result.to_json()) == payload

    def test_failure_payload_teaches_the_model_what_to_do(self) -> None:
        result = ToolResult(
            tool="send_whatsapp_message",
            success=False,
            channel="whatsapp",
            recipient=PHONE,
            error_code=SendErrorCode.RECIPIENT_NOT_VERIFIED,
            error_message="(#131030) Recipient phone number not in allowed list",
            provider_code="131030",
        )
        payload = result.to_payload()
        assert payload["status"] == "failed"
        assert payload["error_code"] == "recipient_not_verified"
        assert payload["provider_code"] == "131030"
        assert payload["retryable"] is False
        assert payload["suggested_fallback_channels"] == ["sms", "email"]
        assert "hint" in payload and payload["hint"]


class TestAddressNormalization:
    """Model-supplied addresses are messy; normalization is not optional."""

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("+15550001111", "+15550001111"),
            ("+1 (555) 000-1111", "+15550001111"),
            ("whatsapp:+14155238886", "+14155238886"),
            ("0015550001111", "+15550001111"),
            ("  15550001111 ", "+15550001111"),
        ],
    )
    def test_phone_accepted(self, raw: str, expected: str) -> None:
        assert normalize_phone(raw) == expected

    @pytest.mark.parametrize("raw", ["", None, "abc", "123", "+1-555-CALL"])
    def test_phone_rejected(self, raw: Any) -> None:
        assert normalize_phone(raw) is None

    def test_default_country_code_is_applied(self) -> None:
        assert normalize_phone("13800138000", "+86") == "+8613800138000"
        assert normalize_phone("013800138000", "+86") == "+8613800138000"

    def test_country_code_not_applied_to_international_numbers(self) -> None:
        assert normalize_phone("+8613800138000", "+1") == "+8613800138000"

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("Patient@Example.COM", "patient@example.com"),
            ("<patient@example.com>", "patient@example.com"),
            (" patient@example.com ", "patient@example.com"),
        ],
    )
    def test_email_accepted(self, raw: str, expected: str) -> None:
        assert normalize_email(raw) == expected

    @pytest.mark.parametrize("raw", ["", None, "not-an-email", "a@b", "@b.com"])
    def test_email_rejected(self, raw: Any) -> None:
        assert normalize_email(raw) is None


class TestValidationFailuresAreReturned:
    """Bad arguments produce feedback, not tracebacks."""

    def test_bad_phone_is_invalid_recipient(self, registry: Any) -> None:
        result = registry.call(
            "send_sms_message", {"recipient": "definitely-not-a-number", "body": "hi"}
        )
        assert result.success is False
        assert result.error_code is SendErrorCode.INVALID_RECIPIENT
        assert result.message_id is None

    def test_unknown_template_lists_the_catalogue(self, registry: Any) -> None:
        result = registry.call(
            "send_whatsapp_message",
            {
                "recipient": PHONE,
                "template": "does_not_exist",
                "params": ["a", "b"],
            },
        )
        assert result.error_code is SendErrorCode.TEMPLATE_NOT_FOUND
        assert result.data["available_templates"]

    def test_wrong_param_count_is_reported_with_counts(self, registry: Any) -> None:
        result = registry.call(
            "send_whatsapp_message",
            {
                "recipient": PHONE,
                "template": "appointment_reminder",
                "params": ["only-one"],
            },
        )
        assert result.error_code is SendErrorCode.TEMPLATE_PARAM_MISMATCH
        assert result.data["expected_param_count"] == 3
        assert result.data["received_param_count"] == 1

    def test_missing_message_content_is_rejected(self, registry: Any) -> None:
        result = registry.call("send_sms_message", {"recipient": PHONE})
        assert result.error_code is SendErrorCode.INVALID_REQUEST

    def test_unknown_tool_lists_valid_tools(self, registry: Any) -> None:
        result = registry.call("send_telegram_message", {"recipient": PHONE})
        assert result.success is False
        assert result.error_code is SendErrorCode.UNKNOWN
        assert "send_whatsapp_message" in result.error_message

    def test_unexpected_argument_is_rejected(self, registry: Any) -> None:
        result = registry.call(
            "send_sms_message",
            {"recipient": PHONE, "body": "hi", "priority": "urgent"},
        )
        assert result.error_code is SendErrorCode.INVALID_REQUEST
        assert "priority" in result.error_message

    def test_missing_required_argument_is_rejected(self, registry: Any) -> None:
        result = registry.call("send_whatsapp_message", {})
        assert result.error_code is SendErrorCode.INVALID_REQUEST
        assert "recipient" in result.error_message

    def test_escalation_requires_a_reason(self, registry: Any) -> None:
        result = registry.call(
            "escalate_to_staff", {"case_id": "C1", "patient_id": "P1", "reason": "  "}
        )
        assert result.success is False
        assert result.error_code is SendErrorCode.INVALID_REQUEST


class TestToolsNeverRaise:
    """Hostile input must still come back as a ``ToolResult``."""

    HOSTILE_ARGUMENTS: Dict[str, Any] = {
        "recipient": None,
        "template": 12345,
        "params": {"not": "a list"},
        "body": b"bytes",
        "subject": None,
        "case_id": [],
        "patient_id": {},
        "reason": None,
        "details": "not a mapping",
        "urgency": 99,
        "preferred_channel": None,
        "failed_channel": "carrier-pigeon",
        "error_code": "not-a-code",
        "language": None,
    }

    @pytest.mark.parametrize("tool", TOOL_NAMES)
    def test_every_tool_survives_hostile_arguments(self, tool: str) -> None:
        registry = make_registry(
            {}, transport=FakeTransport(), smtp=FakeSmtpConnection()
        )
        result = registry.call(tool, self.HOSTILE_ARGUMENTS)
        assert isinstance(result, ToolResult)
        assert isinstance(result.to_payload(), dict)

    @pytest.mark.parametrize("tool", TOOL_NAMES)
    def test_toolkit_methods_themselves_never_raise(self, tool: str) -> None:
        """Bypass the registry guard: the functions must be total on their own."""
        registry = make_registry({})
        func = getattr(registry.toolkit, tool)
        accepted = set(inspect.signature(func).parameters)
        arguments = {
            key: value
            for key, value in self.HOSTILE_ARGUMENTS.items()
            if key in accepted
        }
        result = func(**arguments)
        assert isinstance(result, ToolResult)
        assert isinstance(result.to_payload(), dict)

    @pytest.mark.parametrize("tool", TOOL_NAMES)
    def test_every_tool_works_without_any_credentials(self, tool: str) -> None:
        """Dry-run mode must make an unconfigured install fully usable."""
        registry = make_registry({})
        result = registry.call(
            tool,
            {
                "recipient": PHONE,
                "template": "appointment_reminder",
                "params": ["Alex", "cleaning", "+15550002222"],
                "body": "hi",
                "case_id": "C1",
                "patient_id": "P1",
                "reason": "test",
            },
        )
        assert isinstance(result, ToolResult)

    def test_provider_crash_becomes_an_observable_failure(self, env: dict) -> None:
        """A bug in a provider is reported, never propagated."""

        class ExplodingTransport(FakeTransport):
            def post_json(self, *args: Any, **kwargs: Any) -> Any:
                raise RuntimeError("boom")

            def post_form(self, *args: Any, **kwargs: Any) -> Any:
                raise RuntimeError("boom")

        registry = make_registry(env, transport=ExplodingTransport())
        result = registry.call(
            "send_sms_message", {"recipient": PHONE, "body": "hi"}
        )
        assert isinstance(result, ToolResult)
        assert result.success is False
        assert result.error_code is SendErrorCode.UNKNOWN
        assert "boom" in result.error_message


class TestDryRun:
    """Sends are simulated unless MESSAGING_DRY_RUN=0 explicitly opts in."""

    def test_unconfigured_channel_simulates(self) -> None:
        registry = make_registry({})
        result = registry.call(
            "send_whatsapp_message",
            {
                "recipient": PHONE,
                "template": "appointment_reminder",
                "params": ["Alex", "cleaning", "+15550002222"],
            },
        )
        assert result.success is True
        assert result.simulated is True
        assert result.status == "sent"
        assert result.to_payload()["simulated"] is True
        assert "Dry-run" in result.to_payload()["note"]

    def test_explicit_dry_run_overrides_credentials(self, env: dict) -> None:
        """MESSAGING_DRY_RUN must win even when every credential is present."""
        env["MESSAGING_DRY_RUN"] = "1"
        transport = FakeTransport()
        registry = make_registry(env, transport=transport)
        result = registry.call(
            "send_sms_message", {"recipient": PHONE, "body": "hi"}
        )
        assert result.success is True
        assert result.simulated is True
        assert transport.requests == []

    def test_explicit_live_mode_fails_loudly_when_unconfigured(self) -> None:
        """MESSAGING_DRY_RUN=0 with no credentials must not pretend to send."""
        registry = make_registry({"MESSAGING_DRY_RUN": "0"})
        result = registry.call(
            "send_sms_message", {"recipient": PHONE, "body": "hi"}
        )
        assert result.success is False
        assert result.error_code is SendErrorCode.CONFIG_MISSING
        assert result.retryable is False

    def test_credentials_alone_do_not_enable_live_sending(self, env: dict) -> None:
        """
        A populated .env must never message real patients on its own.

        Live sending is opt-in via MESSAGING_DRY_RUN=0; with the flag absent
        every channel still simulates and no request leaves the process.
        """
        env.pop("MESSAGING_DRY_RUN", None)
        transport = FakeTransport()
        registry = make_registry(env, transport=transport)
        for tool, arguments in (
            ("send_sms_message", {"recipient": PHONE, "body": "hi"}),
            (
                "send_whatsapp_message",
                {"recipient": PHONE, "body": "hi"},
            ),
            (
                "send_email_message",
                {"recipient": EMAIL, "subject": "Hi", "body": "hi"},
            ),
        ):
            result = registry.call(tool, arguments)
            assert result.success is True, tool
            assert result.simulated is True, tool
            assert result.status == "sent", tool
        assert transport.requests == []

    def test_configured_channel_really_sends(self, env: dict) -> None:
        transport = FakeTransport(
            [{"status_code": 201, "body": {"sid": "SMdeadbeef", "status": "queued"}}]
        )
        registry = make_registry(env, transport=transport)
        result = registry.call(
            "send_sms_message", {"recipient": PHONE, "body": "hi"}
        )
        assert result.success is True
        assert result.simulated is False
        assert result.message_id == "SMdeadbeef"
        assert len(transport.requests) == 1
