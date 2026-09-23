"""
Provider-level tests: request building, error classification, transports.

These exercise the real Meta/Twilio/SMTP providers; only the wire is doubled,
so the classifier logic that turns a provider rejection into a normalized
error code is genuinely covered.
"""

from __future__ import annotations
import base64
import json
import smtplib
import ssl
import urllib.request
from pathlib import Path
from typing import Any, Dict

import pytest

from conftest import EMAIL, PHONE, make_registry
from tools.config import MessagingConfig
from tools.errors import SendErrorCode
from tools.providers import (
    MetaWhatsAppProvider,
    SendRequest,
    SmtpEmailProvider,
    TwilioProvider,
    build_default_providers,
)
from tools.transport import FakeSmtpConnection, FakeTransport


def meta_error(code: int, message: str = "rejected") -> Dict[str, Any]:
    """Build a scripted Meta error response."""
    return {
        "status_code": 400,
        "body": {"error": {"message": message, "code": code, "type": "OAuthException"}},
    }


class TestFakeDoubles:
    """The doubles themselves must be trustworthy, or the tests prove nothing."""

    def test_fake_transport_coerces_mappings(self) -> None:
        transport = FakeTransport([{"status_code": 429, "body": {"code": 20429}}])
        response = transport.post_json("https://example.test")
        assert response.status_code == 429
        assert response.json_body()["code"] == 20429

    def test_fake_transport_defaults_to_success_when_exhausted(self) -> None:
        transport = FakeTransport()
        assert transport.post_json("u").ok
        assert transport.post_form("u").ok

    def test_fake_transport_records_requests(self) -> None:
        transport = FakeTransport()
        transport.post_json("https://x", headers={"A": "b"}, payload={"k": "v"})
        assert transport.requests[0]["url"] == "https://x"
        assert transport.requests[0]["payload"] == {"k": "v"}

    def test_queued_connection_failure(self) -> None:
        transport = FakeTransport()
        transport.queue_error("dns failure")
        response = transport.post_json("u")
        assert response.status_code == 0
        assert response.error == "dns failure"
        assert response.ok is False

    def test_smtp_double_records_the_conversation(self) -> None:
        smtp = FakeSmtpConnection()
        smtp.connect("smtp.example.com", 587)
        smtp.ehlo()
        assert smtp.transcript == ["connect:smtp.example.com:587", "ehlo"]


class TestMetaWhatsAppProvider:
    """Meta Cloud API request shape and classification."""

    def _provider(self, env: Dict[str, str], transport: FakeTransport) -> Any:
        return MetaWhatsAppProvider(
            MessagingConfig.from_env(env), transport=transport
        )

    def test_endpoint_uses_version_and_phone_number_id(self, env: dict) -> None:
        provider = self._provider(env, FakeTransport())
        assert provider._endpoint == (
            "https://graph.facebook.com/v21.0/100000000000001/messages"
        )

    def test_templated_payload_matches_the_cloud_api(self, env: dict) -> None:
        transport = FakeTransport()
        provider = self._provider(env, transport)
        provider.send(
            SendRequest(
                to=PHONE,
                template="appointment_reminder",
                params=["Alex", "cleaning", "+15550002222"],
            )
        )
        payload = transport.requests[0]["payload"]
        assert payload["messaging_product"] == "whatsapp"
        assert payload["type"] == "template"
        # Meta wants the number WITHOUT the leading '+'.
        assert payload["to"] == PHONE.lstrip("+")
        assert payload["template"]["name"] == "appointment_reminder"
        assert payload["template"]["language"] == {"code": "en"}
        parameters = payload["template"]["components"][0]["parameters"]
        assert [p["text"] for p in parameters] == [
            "Alex",
            "cleaning",
            "+15550002222",
        ]

    def test_free_form_payload_has_no_template(self, env: dict) -> None:
        transport = FakeTransport()
        self._provider(env, transport).send(SendRequest(to=PHONE, body="hello"))
        payload = transport.requests[0]["payload"]
        assert payload["type"] == "text"
        assert payload["text"]["body"] == "hello"
        assert "template" not in payload

    def test_authorization_header_is_sent(self, env: dict) -> None:
        transport = FakeTransport()
        self._provider(env, transport).send(SendRequest(to=PHONE, body="hi"))
        assert transport.requests[0]["headers"]["Authorization"] == "Bearer test-token"

    def test_success_returns_the_message_id(self, env: dict) -> None:
        transport = FakeTransport(
            [{"status_code": 200, "body": {"messages": [{"id": "wamid.HBgM"}]}}]
        )
        outcome = self._provider(env, transport).send(
            SendRequest(to=PHONE, body="hi")
        )
        assert outcome.success is True
        assert outcome.message_id == "wamid.HBgM"

    @pytest.mark.parametrize(
        "code,expected",
        [
            (131030, SendErrorCode.RECIPIENT_NOT_VERIFIED),
            (131047, SendErrorCode.OUTSIDE_MESSAGING_WINDOW),
            (132001, SendErrorCode.TEMPLATE_NOT_FOUND),
            (190, SendErrorCode.AUTH_FAILED),
            (130429, SendErrorCode.RATE_LIMITED),
        ],
    )
    def test_error_codes_are_normalized(
        self, env: dict, code: int, expected: SendErrorCode
    ) -> None:
        transport = FakeTransport([meta_error(code)])
        outcome = self._provider(env, transport).send(
            SendRequest(to=PHONE, template="appointment_reminder", params=[])
        )
        assert outcome.success is False
        assert outcome.error_code is expected
        assert outcome.provider_code == str(code)

    def test_unknown_numeric_code_falls_back_to_http_status(self, env: dict) -> None:
        transport = FakeTransport([meta_error(999999, "odd")])
        outcome = self._provider(env, transport).send(SendRequest(to=PHONE, body="x"))
        assert outcome.error_code is SendErrorCode.INVALID_REQUEST
        assert outcome.provider_code == "999999"

    def test_404_without_code_is_a_config_problem(self, env: dict) -> None:
        transport = FakeTransport([{"status_code": 404, "body": {}}])
        outcome = self._provider(env, transport).send(SendRequest(to=PHONE, body="x"))
        assert outcome.error_code is SendErrorCode.CONFIG_MISSING

    def test_connection_failure_is_a_network_error(self, env: dict) -> None:
        transport = FakeTransport()
        transport.queue_error("connection refused")
        outcome = self._provider(env, transport).send(SendRequest(to=PHONE, body="x"))
        assert outcome.error_code is SendErrorCode.NETWORK_ERROR
        assert outcome.provider_code == "transport"


class TestTwilioProvider:
    """Twilio request shape and classification, for SMS and WhatsApp."""

    def test_sms_success(self, env: dict) -> None:
        transport = FakeTransport(
            [{"status_code": 201, "body": {"sid": "SMabc", "status": "queued"}}]
        )
        provider = TwilioProvider(
            MessagingConfig.from_env(env), channel="sms", transport=transport
        )
        outcome = provider.send(SendRequest(to=PHONE, body="hi"))
        assert outcome.success is True
        assert outcome.message_id == "SMabc"
        form = transport.requests[0]["data"]
        assert form["To"] == PHONE
        assert form["From"] == "+14155238886"
        assert form["Body"] == "hi"

    def test_credentials_use_basic_auth(self, env: dict) -> None:
        transport = FakeTransport()
        provider = TwilioProvider(
            MessagingConfig.from_env(env), channel="sms", transport=transport
        )
        provider.send(SendRequest(to=PHONE, body="hi"))
        expected = base64.b64encode(
            b"ACtesttesttesttesttesttesttesttest:test-auth-token"
        ).decode()
        assert transport.requests[0]["headers"]["Authorization"] == f"Basic {expected}"
        assert transport.requests[0]["url"].endswith("/Messages.json")

    def test_whatsapp_prefixes_both_sides(self, env: dict) -> None:
        transport = FakeTransport()
        provider = TwilioProvider(
            MessagingConfig.from_env(env), channel="whatsapp", transport=transport
        )
        provider.send(SendRequest(to=PHONE, body="hi"))
        form = transport.requests[0]["data"]
        assert form["To"] == f"whatsapp:{PHONE}"
        assert form["From"] == "whatsapp:+14155238886"

    def test_content_sid_is_preferred_for_templates(self, env: dict) -> None:
        env["TWILIO_CONTENT_SIDS"] = json.dumps(
            {"appointment_reminder": "HX11111111111111111111111111111111"}
        )
        transport = FakeTransport()
        provider = TwilioProvider(
            MessagingConfig.from_env(env), channel="whatsapp", transport=transport
        )
        provider.send(
            SendRequest(
                to=PHONE,
                template="appointment_reminder",
                params=["Alex", "cleaning", "+15550002222"],
            )
        )
        form = transport.requests[0]["data"]
        assert form["ContentSid"] == "HX11111111111111111111111111111111"
        # Twilio ContentVariables are 1-indexed.
        assert json.loads(form["ContentVariables"]) == {
            "1": "Alex",
            "2": "cleaning",
            "3": "+15550002222",
        }
        assert "Body" not in form

    def test_missing_content_sid_falls_back_to_a_body(self, env: dict) -> None:
        transport = FakeTransport()
        provider = TwilioProvider(
            MessagingConfig.from_env(env), channel="whatsapp", transport=transport
        )
        provider.send(
            SendRequest(
                to=PHONE, template="appointment_reminder", params=[], body="hello"
            )
        )
        assert transport.requests[0]["data"]["Body"] == "hello"

    def test_required_content_sid_missing_is_a_tool_error(self, env: dict) -> None:
        env["TWILIO_REQUIRE_CONTENT_SID"] = "1"
        transport = FakeTransport()
        provider = TwilioProvider(
            MessagingConfig.from_env(env), channel="whatsapp", transport=transport
        )
        outcome = provider.send(
            SendRequest(
                to=PHONE, template="appointment_reminder", params=[], body="hello"
            )
        )
        assert outcome.success is False
        assert outcome.error_code is SendErrorCode.TEMPLATE_NOT_FOUND
        assert transport.requests == []

    @pytest.mark.parametrize(
        "code,expected",
        [
            (63016, SendErrorCode.OUTSIDE_MESSAGING_WINDOW),
            (63018, SendErrorCode.RECIPIENT_NOT_VERIFIED),
            (63019, SendErrorCode.OPTED_OUT),
            (21610, SendErrorCode.OPTED_OUT),
            (20429, SendErrorCode.RATE_LIMITED),
            (21211, SendErrorCode.INVALID_RECIPIENT),
            (20003, SendErrorCode.AUTH_FAILED),
            (30001, SendErrorCode.PROVIDER_UNAVAILABLE),
        ],
    )
    def test_error_codes_are_normalized(
        self, env: dict, code: int, expected: SendErrorCode
    ) -> None:
        transport = FakeTransport(
            [
                {
                    "status_code": 400,
                    "body": {"code": code, "message": f"twilio {code}"},
                }
            ]
        )
        provider = TwilioProvider(
            MessagingConfig.from_env(env), channel="sms", transport=transport
        )
        outcome = provider.send(SendRequest(to=PHONE, body="hi"))
        assert outcome.success is False
        assert outcome.error_code is expected
        assert outcome.provider_code == str(code)

    def test_connection_failure_is_a_network_error(self, env: dict) -> None:
        transport = FakeTransport()
        transport.queue_error("timed out")
        provider = TwilioProvider(
            MessagingConfig.from_env(env), channel="sms", transport=transport
        )
        outcome = provider.send(SendRequest(to=PHONE, body="hi"))
        assert outcome.error_code is SendErrorCode.NETWORK_ERROR


class TestSmtpEmailProvider:
    """SMTP sends must fail loudly as data, not as exceptions."""

    def _provider(self, env: dict, smtp: FakeSmtpConnection) -> Any:
        return SmtpEmailProvider(MessagingConfig.from_env(env), lambda: smtp)

    def test_successful_send(self, env: dict) -> None:
        smtp = FakeSmtpConnection()
        outcome = self._provider(env, smtp).send(
            SendRequest(to=EMAIL, subject="Hello", body="body text")
        )
        assert outcome.success is True
        assert outcome.message_id == f"<smtp-{EMAIL}>"
        assert len(smtp.sent) == 1
        message = smtp.sent[0]
        assert message["To"] == EMAIL
        assert message["Subject"] == "Hello"
        assert "body text" in message.get_content()
        assert "starttls" in smtp.transcript
        assert "quit" in smtp.transcript

    def test_recipient_refused_is_invalid_recipient(self, env: dict) -> None:
        smtp = FakeSmtpConnection(
            recipients_refused={EMAIL: (550, b"No such user here")}
        )
        outcome = self._provider(env, smtp).send(SendRequest(to=EMAIL, body="x"))
        assert outcome.success is False
        assert outcome.error_code is SendErrorCode.INVALID_RECIPIENT
        assert "No such user here" in outcome.error_message

    def test_authentication_failure(self, env: dict) -> None:
        smtp = FakeSmtpConnection(auth_failure=True)
        outcome = self._provider(env, smtp).send(SendRequest(to=EMAIL, body="x"))
        assert outcome.error_code is SendErrorCode.AUTH_FAILED

    def test_connect_failure_is_provider_unavailable(self, env: dict) -> None:
        smtp = FakeSmtpConnection(connect_failure=True)
        outcome = self._provider(env, smtp).send(SendRequest(to=EMAIL, body="x"))
        assert outcome.error_code is SendErrorCode.PROVIDER_UNAVAILABLE

    def test_broken_quit_cannot_mask_a_successful_send(self, env: dict) -> None:
        smtp = FakeSmtpConnection(disconnect_on_quit=True)
        outcome = self._provider(env, smtp).send(SendRequest(to=EMAIL, body="x"))
        assert outcome.success is True
        assert smtp.sent

    def test_explicit_ssl_config_changes_the_connection(self, env: dict) -> None:
        env["SMTP_USE_SSL"] = "1"
        env["SMTP_USE_TLS"] = "0"
        smtp = FakeSmtpConnection()
        outcome = self._provider(env, smtp).send(SendRequest(to=EMAIL, body="x"))
        assert outcome.success is True
        assert "starttls" not in smtp.transcript


class TestUrllibTransport:
    """The real transport converts HTTP/connection errors into responses."""

    def test_http_error_becomes_a_readable_response(self) -> None:
        from tools.transport import UrllibTransport

        transport = UrllibTransport()
        response = transport.post_json(
            "https://127.0.0.1:1/definitely-not-listening", timeout=0.5
        )
        assert response.status_code == 0
        assert response.error
        assert response.ok is False


class TestProviderAssembly:
    """Channel wiring follows ``WHATSAPP_PROVIDER``."""

    def test_meta_is_the_default_whatsapp_provider(self, env: dict) -> None:
        providers = build_default_providers(MessagingConfig.from_env(env))
        assert isinstance(providers["whatsapp"], MetaWhatsAppProvider)
        assert isinstance(providers["sms"], TwilioProvider)
        assert isinstance(providers["email"], SmtpEmailProvider)

    def test_twilio_whatsapp_alternative(self, env: dict) -> None:
        env["WHATSAPP_PROVIDER"] = "twilio"
        providers = build_default_providers(MessagingConfig.from_env(env))
        assert providers["whatsapp"].channel == "whatsapp"
        assert isinstance(providers["whatsapp"], TwilioProvider)

    def test_none_disables_whatsapp(self, env: dict) -> None:
        env["WHATSAPP_PROVIDER"] = "none"
        providers = build_default_providers(MessagingConfig.from_env(env))
        assert "whatsapp" not in providers

    def test_disabled_channel_reports_provider_unavailable(self, env: dict) -> None:
        env["WHATSAPP_PROVIDER"] = "none"
        registry = make_registry(env)
        result = registry.call(
            "send_whatsapp_message",
            {
                "recipient": PHONE,
                "template": "appointment_reminder",
                "params": ["a", "b", "c"],
            },
        )
        assert result.success is False
        assert result.error_code is SendErrorCode.PROVIDER_UNAVAILABLE


class TestTlsTrust:
    """
    TLS must verify peers on a machine whose system trust store is empty.

    A macOS python.org install ships no CAs until ``Install Certificates`` is
    run, and the failure it causes (``unable to get local issuer certificate``)
    is indistinguishable from a provider outage at the call site. These tests
    pin the bundle-selection rules that keep real sends working.
    """

    def test_ssl_cert_file_wins_when_it_exists(self, tmp_path, monkeypatch) -> None:
        from tools import tls

        bundle = tmp_path / "custom.pem"
        bundle.write_text("dummy")
        monkeypatch.setenv("SSL_CERT_FILE", str(bundle))
        assert tls.ca_bundle_path() == str(bundle)

    def test_missing_ssl_cert_file_falls_back_to_certifi(self, monkeypatch) -> None:
        from tools import tls

        monkeypatch.setenv("SSL_CERT_FILE", "/nonexistent/nowhere.pem")
        resolved = tls.ca_bundle_path()
        # Either certifi supplied a real file, or no bundle is available at all;
        # what must never happen is pointing OpenSSL at a file that is not there.
        assert resolved is None or Path(resolved).is_file()

    def test_certifi_bundle_is_used_when_present(self, monkeypatch) -> None:
        from tools import tls

        monkeypatch.delenv("SSL_CERT_FILE", raising=False)
        try:
            import certifi
        except ImportError:
            pytest.skip("certifi is not installed")
        assert tls.ca_bundle_path() == certifi.where()

    def test_context_can_actually_verify_peers(self) -> None:
        from tools import tls

        context = tls.default_ssl_context()
        assert context.verify_mode == ssl.CERT_REQUIRED
        assert context.check_hostname is True
        assert context.cert_store_stats()["x509_ca"] > 0

    def test_context_is_reused(self) -> None:
        from tools import tls

        assert tls.default_ssl_context() is tls.default_ssl_context()

    def test_reset_cache_drops_the_context(self) -> None:
        from tools import tls

        first = tls.default_ssl_context()
        tls.reset_cache()
        try:
            assert tls.default_ssl_context() is not first
        finally:
            tls.reset_cache()

    def test_smtp_over_ssl_uses_the_verifying_context(self, env: dict, monkeypatch) -> None:
        from tools import tls

        env["SMTP_USE_SSL"] = "1"
        env["SMTP_USE_TLS"] = "0"
        captured: Dict[str, Any] = {}

        def fake_smtp_ssl(**kwargs: Any) -> FakeSmtpConnection:
            captured.update(kwargs)
            return FakeSmtpConnection()

        monkeypatch.setattr(smtplib, "SMTP_SSL", fake_smtp_ssl)
        SmtpEmailProvider(MessagingConfig.from_env(env))._default_factory()
        assert captured["context"] is tls.default_ssl_context()

    def test_starttls_uses_the_verifying_context(self, env: dict, monkeypatch) -> None:
        from tools import tls

        env["SMTP_USE_SSL"] = "0"
        env["SMTP_USE_TLS"] = "1"
        captured: Dict[str, Any] = {}

        class RecordingSmtp(FakeSmtpConnection):
            def starttls(self, **kwargs: Any) -> Any:
                captured.update(kwargs)
                self.transcript.append("starttls")
                return (220, b"ready")

        monkeypatch.setattr(smtplib, "SMTP", lambda **kwargs: RecordingSmtp())
        provider = SmtpEmailProvider(MessagingConfig.from_env(env))
        outcome = provider.send(SendRequest(to=EMAIL, body="x"))
        assert outcome.success is True
        assert captured["context"] is tls.default_ssl_context()

    def test_urllib_transport_passes_a_context(self, monkeypatch) -> None:
        from tools import tls
        from tools.transport import UrllibTransport

        captured: Dict[str, Any] = {}

        class FakeResponse:
            status = 200
            headers: Dict[str, str] = {}

            def read(self) -> bytes:
                return b'{"ok": true}'

            def __enter__(self) -> "FakeResponse":
                return self

            def __exit__(self, *exc: Any) -> None:
                return None

        def fake_urlopen(request: Any, **kwargs: Any) -> FakeResponse:
            captured.update(kwargs)
            return FakeResponse()

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        response = UrllibTransport().post_json("https://example.test/hook")
        assert response.status_code == 200
        assert captured["context"] is tls.default_ssl_context()
