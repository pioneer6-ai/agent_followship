"""
Tests for the AWS-backed tools: ``send_sms`` and ``send_email``.

Everything here is offline. The boto3 clients are fakes that record the exact
keyword arguments they receive and raise scripted ``ClientError``s, so the real
guard order, request building, error classification and audit trail all execute
without credentials and without a network call.
"""

from __future__ import annotations
from typing import Any, Dict, List, Optional

import pytest
from botocore.exceptions import ClientError

from tests.conftest import AWS_ENV, AWS_TEST_EMAIL, AWS_TEST_PHONE, make_registry
from tools.config import MessagingConfig
from tools.errors import SendErrorCode, from_boto_error
from tools.messaging import TOOL_NAMES, build_tool_registry
from tools.schemas import TOOL_SCHEMAS, validate_schema_coverage


def client_error(code: str, message: str = "boom") -> ClientError:
    """Build a ``ClientError`` shaped exactly like a real AWS one."""
    return ClientError({"Error": {"Code": code, "Message": message}}, "SendCommand")


class FakeAwsClient:
    """Records calls and returns a scripted response or raises a scripted error."""

    def __init__(
        self,
        response: Optional[Dict[str, Any]] = None,
        error: Optional[BaseException] = None,
    ) -> None:
        self.response = response if response is not None else {"MessageId": "msg-1"}
        self.error = error
        self.calls: List[Dict[str, Any]] = []

    def _record(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return dict(self.response)

    def send_text_message(self, **kwargs: Any) -> Dict[str, Any]:
        return self._record(kwargs)

    def send_email(self, **kwargs: Any) -> Dict[str, Any]:
        return self._record(kwargs)


@pytest.fixture
def aws_env() -> Dict[str, str]:
    """Live-sending credentials, with the AWS block pinned to the test pair."""
    env = dict(AWS_ENV)
    env["MESSAGING_DRY_RUN"] = "0"
    return env


@pytest.fixture
def fake_sms() -> FakeAwsClient:
    return FakeAwsClient({"MessageId": "sms-123"})


@pytest.fixture
def fake_email() -> FakeAwsClient:
    return FakeAwsClient({"MessageId": "ses-456"})


@pytest.fixture
def registry(aws_env: Dict[str, str], fake_sms: FakeAwsClient, fake_email: FakeAwsClient):
    """Registry whose AWS channels are backed by fake clients."""
    return make_registry(
        aws_env, aws_clients={"sms": fake_sms, "email": fake_email}
    )


def sms_args(**overrides: Any) -> Dict[str, Any]:
    """Valid ``send_sms`` arguments, with overrides."""
    args: Dict[str, Any] = {
        "phone_number": AWS_TEST_PHONE,
        "message": "Hi Alex, this is Bright Smile: time for your 6-month check-up.",
        "reason": "patient is 7 months past their last visit",
    }
    args.update(overrides)
    return args


def email_args(**overrides: Any) -> Dict[str, Any]:
    """Valid ``send_email`` arguments, with overrides."""
    args: Dict[str, Any] = {
        "to_email": AWS_TEST_EMAIL,
        "subject": "Time for your check-up",
        "body": "Hi Alex, our records show it has been a while since your last visit.",
        "reason": "second recall attempt after SMS went unanswered",
    }
    args.update(overrides)
    return args


class TestSendSmsHappyPath:
    """``send_sms`` reaches ``send_text_message`` with the right arguments."""

    def test_returns_sent_with_the_message_id(self, registry, fake_sms):
        result = registry.call("send_sms", sms_args())

        assert result.success is True
        assert result.status == "sent"
        assert result.message_id == "sms-123"
        assert result.simulated is False
        assert result.error_code is None

    def test_uses_the_transactional_message_type(self, registry, fake_sms):
        registry.call("send_sms", sms_args())

        assert fake_sms.calls[0]["MessageType"] == "TRANSACTIONAL"

    def test_sends_to_the_normalized_destination(self, registry, fake_sms):
        registry.call("send_sms", sms_args(phone_number="65 8353 6885"))

        assert fake_sms.calls[0]["DestinationPhoneNumber"] == AWS_TEST_PHONE

    def test_message_body_is_the_agent_text(self, registry, fake_sms):
        registry.call("send_sms", sms_args(message="Come back for a check-up."))

        assert fake_sms.calls[0]["MessageBody"] == "Come back for a check-up."

    def test_omits_origination_identity_when_unset(self, registry, fake_sms):
        registry.call("send_sms", sms_args())

        assert "OriginationIdentity" not in fake_sms.calls[0]

    def test_includes_origination_identity_when_configured(self, fake_sms, fake_email):
        env = dict(AWS_ENV)
        env["MESSAGING_DRY_RUN"] = "0"
        env["AWS_SMS_ORIGINATION_IDENTITY"] = "+14155238886"
        reg = make_registry(env, aws_clients={"sms": fake_sms, "email": fake_email})

        reg.call("send_sms", sms_args())

        assert fake_sms.calls[0]["OriginationIdentity"] == "+14155238886"

    def test_exactly_one_attempt(self, registry, fake_sms):
        registry.call("send_sms", sms_args())

        assert len(fake_sms.calls) == 1


class TestSendEmailHappyPath:
    """``send_email`` reaches ``send_email`` with the SES message shape."""

    def test_returns_sent_with_the_message_id(self, registry, fake_email):
        result = registry.call("send_email", email_args())

        assert result.success is True
        assert result.message_id == "ses-456"
        assert result.simulated is False

    def test_uses_the_verified_source(self, registry, fake_email):
        registry.call("send_email", email_args())

        assert fake_email.calls[0]["Source"] == AWS_TEST_EMAIL

    def test_builds_the_ses_message_shape(self, registry, fake_email):
        registry.call(
            "send_email", email_args(subject="Check-up", body="Please book.")
        )

        call = fake_email.calls[0]
        assert call["Destination"] == {"ToAddresses": [AWS_TEST_EMAIL]}
        assert call["Message"]["Subject"]["Data"] == "Check-up"
        assert call["Message"]["Body"]["Text"]["Data"] == "Please book."

    def test_exactly_one_attempt(self, registry, fake_email):
        registry.call("send_email", email_args())

        assert len(fake_email.calls) == 1


class TestAllowList:
    """The verified-recipient allow-list is a hard, fail-closed gate."""

    def test_refuses_an_unlisted_phone_number(self, registry, fake_sms):
        result = registry.call("send_sms", sms_args(phone_number="+15550001111"))

        assert result.success is False
        assert result.error_code == SendErrorCode.RECIPIENT_NOT_VERIFIED
        assert result.provider_code == "allowlist"
        assert fake_sms.calls == []

    def test_refuses_an_unlisted_email_address(self, registry, fake_email):
        result = registry.call("send_email", email_args(to_email="patient@example.com"))

        assert result.error_code == SendErrorCode.RECIPIENT_NOT_VERIFIED
        assert result.provider_code == "allowlist"
        assert fake_email.calls == []

    def test_refusal_names_the_allow_list(self, registry):
        result = registry.call("send_sms", sms_args(phone_number="+15550001111"))

        assert result.data["allowlist"] == "verified SMS recipient allow-list"

    def test_refusal_suggests_another_channel(self, registry):
        result = registry.call("send_sms", sms_args(phone_number="+15550001111"))

        assert result.suggested_fallback_channels == ["email"]

    def test_email_is_matched_case_insensitively(self, registry, fake_email):
        result = registry.call(
            "send_email", email_args(to_email="MartinChenOnly1@Gmail.Com")
        )

        assert result.success is True
        assert fake_email.calls[0]["Destination"] == {"ToAddresses": [AWS_TEST_EMAIL]}

    def test_blank_env_keeps_the_default_allow_list(self, fake_sms, fake_email):
        """An empty variable is a no-op, not an accidental opening of the gate."""
        env = dict(AWS_ENV)
        env["MESSAGING_DRY_RUN"] = "0"
        env["AWS_SMS_ALLOWED_NUMBERS"] = ""
        env["AWS_EMAIL_ALLOWED_ADDRESSES"] = ""
        reg = make_registry(env, aws_clients={"sms": fake_sms, "email": fake_email})

        assert reg.call("send_sms", sms_args()).success is True
        assert reg.call("send_email", email_args()).success is True
        assert reg.call("send_sms", sms_args(phone_number="+15550001111")).success is False

    def test_empty_allow_list_denies_everything(self, fake_sms, fake_email):
        config = MessagingConfig.from_env(AWS_ENV)
        config.aws_sms_allowlist = []
        config.aws_email_allowlist = []
        reg = build_tool_registry(
            config, aws_clients={"sms": fake_sms, "email": fake_email}
        )

        sms = reg.call("send_sms", sms_args())
        email = reg.call("send_email", email_args())

        assert sms.error_code == SendErrorCode.RECIPIENT_NOT_VERIFIED
        assert email.error_code == SendErrorCode.RECIPIENT_NOT_VERIFIED
        assert fake_sms.calls == []
        assert fake_email.calls == []


class TestValidation:
    """Unusable input is refused before the provider is involved."""

    @pytest.mark.parametrize("bad", [None, "", "   ", "not-a-number", "+"])
    def test_bad_phone_number(self, registry, fake_sms, bad):
        result = registry.call("send_sms", sms_args(phone_number=bad))

        assert result.error_code == SendErrorCode.INVALID_RECIPIENT
        assert fake_sms.calls == []

    @pytest.mark.parametrize("bad", [None, "", "   ", "not-an-email", "@example.com"])
    def test_bad_email_address(self, registry, fake_email, bad):
        result = registry.call("send_email", email_args(to_email=bad))

        assert result.error_code == SendErrorCode.INVALID_RECIPIENT
        assert fake_email.calls == []

    @pytest.mark.parametrize("bad", [None, "", "   ", "\n\t "])
    def test_blank_message_is_invalid_request(self, registry, fake_sms, bad):
        result = registry.call("send_sms", sms_args(message=bad))

        assert result.error_code == SendErrorCode.INVALID_REQUEST
        assert fake_sms.calls == []

    @pytest.mark.parametrize("bad", [None, "", "   "])
    def test_blank_subject_is_invalid_request(self, registry, fake_email, bad):
        result = registry.call("send_email", email_args(subject=bad))

        assert result.error_code == SendErrorCode.INVALID_REQUEST
        assert fake_email.calls == []

    @pytest.mark.parametrize("bad", [None, "", "   "])
    def test_blank_body_is_invalid_request(self, registry, fake_email, bad):
        result = registry.call("send_email", email_args(body=bad))

        assert result.error_code == SendErrorCode.INVALID_REQUEST
        assert fake_email.calls == []

    def test_recipient_is_validated_before_the_text(self, registry, fake_sms):
        result = registry.call(
            "send_sms", sms_args(phone_number="junk", message="   ")
        )

        assert result.error_code == SendErrorCode.INVALID_RECIPIENT

    def test_long_message_is_clipped_not_rejected(self, registry, fake_sms):
        result = registry.call("send_sms", sms_args(message="x" * 5000))

        assert result.success is True
        assert len(fake_sms.calls[0]["MessageBody"]) <= 4096


class TestErrorHandling:
    """Provider errors become data the agent can reason about."""

    def test_subscription_required_is_reported_not_raised(
        self, aws_env, fake_email
    ):
        sms = FakeAwsClient(
            error=client_error(
                "SubscriptionRequiredException",
                "The AWS Access Key Id needs a subscription for the service",
            )
        )
        reg = make_registry(aws_env, aws_clients={"sms": sms, "email": fake_email})

        result = reg.call("send_sms", sms_args())

        assert result.success is False
        assert result.error_code == SendErrorCode.NOT_SUBSCRIBED
        assert result.provider_code == "SubscriptionRequiredException"

    def test_not_subscribed_is_not_retryable(self, aws_env, fake_email):
        sms = FakeAwsClient(error=client_error("SubscriptionRequiredException"))
        reg = make_registry(aws_env, aws_clients={"sms": sms, "email": fake_email})

        result = reg.call("send_sms", sms_args())

        assert result.retryable is False
        assert result.suggested_fallback_channels == []

    def test_not_subscribed_hint_mentions_escalation(self, aws_env, fake_email):
        sms = FakeAwsClient(error=client_error("SubscriptionRequiredException"))
        reg = make_registry(aws_env, aws_clients={"sms": sms, "email": fake_email})

        result = reg.call("send_sms", sms_args())

        assert "escalate to staff" in result.hint

    def test_ses_sandbox_rejection_maps_to_not_verified(self, aws_env, fake_sms):
        email = FakeAwsClient(
            error=client_error(
                "MessageRejected",
                "Email address is not verified. "
                "The following identities failed the check in region "
                "AP-SOUTHEAST-1: patient@example.com",
            )
        )
        reg = make_registry(aws_env, aws_clients={"sms": fake_sms, "email": email})

        result = reg.call("send_email", email_args())

        assert result.error_code == SendErrorCode.RECIPIENT_NOT_VERIFIED
        assert result.suggested_fallback_channels == ["sms"]

    def test_throttling_is_retryable(self, aws_env, fake_email):
        sms = FakeAwsClient(error=client_error("ThrottlingException", "rate exceeded"))
        reg = make_registry(aws_env, aws_clients={"sms": sms, "email": fake_email})

        result = reg.call("send_sms", sms_args())

        assert result.error_code == SendErrorCode.RATE_LIMITED
        assert result.retryable is True

    def test_access_denied_maps_to_auth_failed(self, aws_env, fake_email):
        sms = FakeAwsClient(error=client_error("AccessDeniedException"))
        reg = make_registry(aws_env, aws_clients={"sms": sms, "email": fake_email})

        result = reg.call("send_sms", sms_args())

        assert result.error_code == SendErrorCode.AUTH_FAILED
        assert result.retryable is False

    def test_provider_message_is_preserved_verbatim(self, aws_env, fake_email):
        sms = FakeAwsClient(error=client_error("ThrottlingException", "Slow down"))
        reg = make_registry(aws_env, aws_clients={"sms": sms, "email": fake_email})

        result = reg.call("send_sms", sms_args())

        assert result.error_message == "Slow down"

    def test_endpoint_connection_error_is_a_network_failure(
        self, aws_env, fake_email
    ):
        from botocore.exceptions import EndpointConnectionError

        sms = FakeAwsClient(
            error=EndpointConnectionError(endpoint_url="https://example.invalid")
        )
        reg = make_registry(aws_env, aws_clients={"sms": sms, "email": fake_email})

        result = reg.call("send_sms", sms_args())

        assert result.error_code == SendErrorCode.NETWORK_ERROR
        assert result.retryable is True

    def test_non_aws_bug_becomes_unknown_not_misclassified(self, aws_env, fake_email):
        sms = FakeAwsClient(error=TypeError("a bug in our own code"))
        reg = make_registry(aws_env, aws_clients={"sms": sms, "email": fake_email})

        result = reg.call("send_sms", sms_args())

        assert result.error_code == SendErrorCode.UNKNOWN
        assert result.success is False

    def test_a_failed_send_stays_observable_in_the_audit_trail(
        self, aws_env, fake_email
    ):
        sms = FakeAwsClient(error=client_error("ThrottlingException"))
        reg = make_registry(aws_env, aws_clients={"sms": sms, "email": fake_email})

        reg.call("send_sms", sms_args())

        assert reg.history[-1]["error_code"] == "rate_limited"


class TestMissingBoto3:
    """Without boto3 the tools still work -- they just cannot transmit."""

    def test_reports_config_missing(self, aws_env, monkeypatch):
        import tools.aws_providers as aws_providers

        def explode() -> Any:
            raise ImportError("No module named 'boto3'")

        monkeypatch.setattr(aws_providers, "load_boto3", explode)
        reg = make_registry(aws_env)

        result = reg.call("send_sms", sms_args())

        assert result.success is False
        assert result.error_code == SendErrorCode.CONFIG_MISSING
        assert "boto3" in result.error_message

    def test_does_not_raise(self, aws_env, monkeypatch):
        import tools.aws_providers as aws_providers

        def explode() -> Any:
            raise ImportError("No module named 'boto3'")

        monkeypatch.setattr(aws_providers, "load_boto3", explode)
        reg = make_registry(aws_env)

        assert reg.call("send_sms", sms_args()).status == "failed"
        assert reg.call("send_email", email_args()).status == "failed"


class TestDryRun:
    """The safe default: no credentials, no client, no network."""

    def test_dry_run_reports_simulated(self):
        env = dict(AWS_ENV)
        env["MESSAGING_DRY_RUN"] = "1"
        reg = make_registry(env)

        result = reg.call("send_sms", sms_args())

        assert result.success is True
        assert result.simulated is True

    def test_dry_run_makes_no_aws_call(self):
        calls: List[Dict[str, Any]] = []

        class Exploding(FakeAwsClient):
            def _record(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
                calls.append(kwargs)
                raise AssertionError("dry run must not reach AWS")

        env = dict(AWS_ENV)
        env["MESSAGING_DRY_RUN"] = "1"
        reg = make_registry(env, aws_clients={"sms": Exploding(), "email": Exploding()})

        assert reg.call("send_sms", sms_args()).simulated is True
        assert reg.call("send_email", email_args()).simulated is True
        assert calls == []

    def test_dry_run_builds_no_client(self, monkeypatch):
        """Even without boto3 at all, dry-run succeeds."""
        import tools.aws_providers as aws_providers

        def explode() -> Any:
            raise ImportError("No module named 'boto3'")

        monkeypatch.setattr(aws_providers, "load_boto3", explode)
        env = dict(AWS_ENV)
        env["MESSAGING_DRY_RUN"] = "1"
        reg = make_registry(env)

        assert reg.call("send_sms", sms_args()).success is True

    def test_dry_run_still_enforces_the_allow_list(self):
        env = dict(AWS_ENV)
        env["MESSAGING_DRY_RUN"] = "1"
        reg = make_registry(env)

        result = reg.call("send_sms", sms_args(phone_number="+15550001111"))

        assert result.error_code == SendErrorCode.RECIPIENT_NOT_VERIFIED


class TestReasonLogging:
    """The agent's justification is logged, audited and echoed back."""

    def test_reason_is_printed(self, registry, capsys):
        registry.call("send_sms", sms_args(reason="6-month recall due"))

        assert "[Agent决策] 6-month recall due" in capsys.readouterr().out

    def test_reason_is_printed_on_refusal(self, registry, capsys):
        registry.call("send_sms", sms_args(phone_number="+15550001111", reason="why"))

        assert "[Agent决策] why" in capsys.readouterr().out

    def test_reason_is_printed_on_provider_failure(self, aws_env, fake_email, capsys):
        sms = FakeAwsClient(error=client_error("ThrottlingException"))
        reg = make_registry(aws_env, aws_clients={"sms": sms, "email": fake_email})

        reg.call("send_sms", sms_args(reason="third attempt today"))

        assert "[Agent决策] third attempt today" in capsys.readouterr().out

    def test_reason_is_returned_to_the_model(self, registry):
        result = registry.call("send_email", email_args(reason="no reply to SMS"))

        assert result.data["reason"] == "no reply to SMS"

    def test_reason_is_in_the_audit_trail(self, registry):
        registry.call("send_sms", sms_args(reason="overdue cleaning"))

        assert registry.history[-1]["reason"] == "overdue cleaning"

    def test_missing_reason_is_not_a_failure(self, registry):
        result = registry.call(
            "send_sms",
            {"phone_number": AWS_TEST_PHONE, "message": "Hi Alex."},
        )

        assert result.success is True
        assert result.data["reason"] == "(unspecified)"

    def test_missing_reason_is_recorded_as_unspecified(self, registry):
        registry.call("send_email", email_args(reason=None))

        assert registry.history[-1]["reason"] == "(unspecified)"

    def test_reason_is_trimmed(self, registry):
        result = registry.call("send_sms", sms_args(reason="  spaced out  "))

        assert result.data["reason"] == "spaced out"


class TestReasonDoesNotAffectOtherTools:
    """The Twilio/SMTP tools keep their previous behaviour."""

    def test_whatsapp_audit_entry_has_no_reason_key(self, env):
        reg = make_registry(env)
        reg.call(
            "send_whatsapp_message",
            {"recipient": "+15550001111", "body": "hello"},
        )

        assert "reason" not in reg.history[-1]


class TestSchemas:
    """Both tools are registered exactly as requested."""

    @pytest.mark.parametrize("name", ["send_sms", "send_email"])
    def test_tool_is_registered(self, name):
        assert name in TOOL_NAMES
        assert name in TOOL_SCHEMAS

    def test_no_schema_drift(self):
        assert validate_schema_coverage() == []

    def test_sms_required_fields(self):
        schema = TOOL_SCHEMAS["send_sms"]["input_schema"]

        assert schema["required"] == ["phone_number", "message", "reason"]
        assert set(schema["properties"]) == {"phone_number", "message", "reason"}

    def test_email_required_fields(self):
        schema = TOOL_SCHEMAS["send_email"]["input_schema"]

        assert schema["required"] == ["to_email", "subject", "body", "reason"]
        assert set(schema["properties"]) == {
            "to_email",
            "subject",
            "body",
            "reason",
        }

    @pytest.mark.parametrize("name", ["send_sms", "send_email"])
    def test_schema_teaches_the_failure_protocol(self, name):
        description = TOOL_SCHEMAS[name]["description"]

        assert "never raises" in description
        assert "error_code" in description
        assert "suggested_fallback_channels" in description

    @pytest.mark.parametrize("name", ["send_sms", "send_email"])
    def test_schema_says_the_recipient_is_restricted(self, name):
        assert "sandbox" in TOOL_SCHEMAS[name]["description"]

    def test_registry_offers_both_tools(self, registry):
        assert "send_sms" in registry.names
        assert "send_email" in registry.names

    def test_old_channel_tools_are_kept(self, registry):
        assert {"send_whatsapp_message", "send_sms_message", "send_email_message"} <= set(
            registry.names
        )


class TestErrorCodeTable:
    """The AWS code table maps the codes the real APIs return."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("SubscriptionRequiredException", SendErrorCode.NOT_SUBSCRIBED),
            ("MessageRejected", SendErrorCode.RECIPIENT_NOT_VERIFIED),
            ("ThrottlingException", SendErrorCode.RATE_LIMITED),
            ("TooManyRequestsException", SendErrorCode.RATE_LIMITED),
            ("AccessDeniedException", SendErrorCode.AUTH_FAILED),
            ("ExpiredTokenException", SendErrorCode.AUTH_FAILED),
            ("ValidationException", SendErrorCode.INVALID_REQUEST),
            ("ServiceUnavailable", SendErrorCode.PROVIDER_UNAVAILABLE),
            ("InternalServiceError", SendErrorCode.PROVIDER_UNAVAILABLE),
            ("EndpointConnectionError", SendErrorCode.NETWORK_ERROR),
            ("SomethingBrandNewException", SendErrorCode.UNKNOWN),
        ],
    )
    def test_mapping(self, raw, expected):
        assert from_boto_error(raw) == expected

    def test_matching_is_case_insensitive(self):
        assert from_boto_error("throttlingexception") == SendErrorCode.RATE_LIMITED

    def test_throttle_substring_falls_back(self):
        assert from_boto_error("SlowDownThrottling") == SendErrorCode.RATE_LIMITED

    def test_unknown_input_is_unknown(self):
        assert from_boto_error(None) == SendErrorCode.UNKNOWN


class TestToolkitMethodsAreTotal:
    """The tools never raise, whatever the model sends."""

    @pytest.mark.parametrize("name", ["send_sms", "send_email"])
    @pytest.mark.parametrize(
        "arguments",
        [
            None,
            {},
            {"phone_number": None, "message": None, "reason": None},
            {"to_email": [], "subject": {}, "body": 5, "reason": 0},
            {"phone_number": {"nested": "dict"}, "message": ["list"], "reason": True},
            {"phone_number": "+6583536885", "message": "x" * 100000, "reason": "y"},
        ],
    )
    def test_never_raises(self, registry, name, arguments):
        result = registry.call(name, arguments)

        assert result.tool == name
        assert result.status in {"sent", "failed"}


class TestConfigDefaults:
    """The allow-list and region defaults match the verified test pair."""

    def test_default_allow_lists(self):
        config = MessagingConfig.from_env({})

        assert config.is_sms_recipient_allowed(AWS_TEST_PHONE) is True
        assert config.is_email_recipient_allowed(AWS_TEST_EMAIL) is True

    def test_default_region(self):
        assert MessagingConfig.from_env({}).aws_region == "ap-southeast-1"

    def test_region_is_overridable(self):
        env = {"AWS_REGION": "us-east-1"}

        assert MessagingConfig.from_env(env).aws_region == "us-east-1"

    def test_aws_default_region_is_honoured(self):
        env = {"AWS_DEFAULT_REGION": "eu-west-1"}

        assert MessagingConfig.from_env(env).aws_region == "eu-west-1"

    def test_unlisted_recipients_are_denied_by_default(self):
        config = MessagingConfig.from_env({})

        assert config.is_sms_recipient_allowed("+15550001111") is False
        assert config.is_email_recipient_allowed("patient@example.com") is False
