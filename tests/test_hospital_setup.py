"""
Tests for the hospital-facing interface file (``hospital_setup.py``).

This is the file a clinic edits, so its failure modes are the ones that matter:
a half-filled mailbox must be *reported*, and no single edit may start messaging
patients. The tests therefore concentrate on ``environment()`` / ``validate()``
and on the live-sending gate, which is the safety property -- an accident there
would contact real patients directly.

The settings are module-level globals (that is the whole point of the file), so
each test substitutes them and lets monkeypatch restore them.
"""

import inspect
import os

import pytest

import hospital_setup as hs
from hospital_setup import (
    AgentSettings,
    EmailSettings,
    LlmSettings,
    SMTP_PRESETS,
    environment,
    validate,
)


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch):
    """
    Give every test its own ``os.environ``.

    ``apply()`` writes to the real environment by design, so without this a test
    would leak ``AGENT_LIVE_SENDS=1`` / ``MESSAGING_DRY_RUN=0`` into the rest of
    the session -- which is both a failing-test generator and, for a suite that
    can simulate sending, a genuinely unsafe thing to leave lying around.
    """
    monkeypatch.setattr(os, "environ", dict(os.environ))


@pytest.fixture
def configured(monkeypatch):
    """Replace the three settings blocks with a coherent live configuration."""
    monkeypatch.setattr(
        hs,
        "LLM",
        LlmSettings(provider="openai", model="gpt-4o-mini", api_key="sk-test"),
    )
    monkeypatch.setattr(
        hs,
        "EMAIL",
        EmailSettings(
            enabled=True,
            address="reminders@clinic.example",
            display_name="BrightSmile Dental",
            preset="microsoft365",
            smtp_password="app-password",
        ),
    )
    monkeypatch.setattr(
        hs,
        "AGENT",
        AgentSettings(
            escalation_email="staff@clinic.example",
            enable_live_sending=True,
            live_sends_acknowledged=True,
        ),
    )


# ---------------------------------------------------------------------------
# The defaults must be inert
# ---------------------------------------------------------------------------


class TestShippedDefaults:
    def test_the_shipped_settings_validate_as_incomplete_but_not_dangerous(
        self, monkeypatch
    ):
        # As shipped, nothing is configured -- and crucially, nothing sends.
        # The repo's own .env is cleared first: this asserts the *file's*
        # defaults, not whatever the developer happens to have exported.
        for name in (
            "AGENT_LLM_API_KEY",
            "LLM_API_KEY",
            "ANTHROPIC_API_KEY",
            "OPENAI_API_KEY",
            "SMTP_PASSWORD",
        ):
            monkeypatch.delenv(name, raising=False)
        problems = validate()
        assert any("No LLM credential" in p for p in problems)
        assert any("EMAIL.enabled is False" in p for p in problems)
        assert environment()["AGENT_LIVE_SENDS"] == "0"
        assert environment()["MESSAGING_DRY_RUN"] == "1"

    def test_the_shipped_email_block_is_disabled(self):
        assert EmailSettings().enabled is False

    def test_the_shipped_agent_blocks_live_sending_twice(self):
        defaults = AgentSettings()
        assert defaults.enable_live_sending is False
        assert defaults.live_sends_acknowledged is False


# ---------------------------------------------------------------------------
# The live-sending gate: the safety property
# ---------------------------------------------------------------------------


class TestLiveSendingGate:
    def test_a_fully_configured_clinic_gets_live_sending(self, configured):
        env = environment()
        assert env["AGENT_LIVE_SENDS"] == "1"
        assert env["MESSAGING_DRY_RUN"] == "0"

    def test_only_the_first_switch_leaves_the_agent_simulating(self, monkeypatch):
        monkeypatch.setattr(
            hs, "AGENT", AgentSettings(enable_live_sending=True)
        )
        env = environment()
        assert env["AGENT_LIVE_SENDS"] == "0"
        assert env["MESSAGING_DRY_RUN"] == "1"

    def test_only_the_second_switch_leaves_the_agent_simulating(self, monkeypatch):
        monkeypatch.setattr(
            hs, "AGENT", AgentSettings(live_sends_acknowledged=True)
        )
        env = environment()
        assert env["AGENT_LIVE_SENDS"] == "0"
        assert env["MESSAGING_DRY_RUN"] == "1"

    def test_neither_switch_leaves_the_agent_simulating(self):
        assert environment()["AGENT_LIVE_SENDS"] == "0"

    def test_a_half_acknowledged_live_setting_is_reported(self, monkeypatch):
        monkeypatch.setattr(
            hs, "AGENT", AgentSettings(enable_live_sending=True)
        )
        assert any(
            "live_sends_acknowledged" in p for p in validate()
        )


# ---------------------------------------------------------------------------
# validate()
# ---------------------------------------------------------------------------


class TestValidate:
    def test_a_coherent_configuration_reports_nothing(self, configured):
        assert validate() == []

    def test_live_sending_without_an_escalation_address_is_refused(
        self, configured, monkeypatch
    ):
        # A human must be told when no channel can reach a patient.
        monkeypatch.setattr(
            hs,
            "AGENT",
            AgentSettings(enable_live_sending=True, live_sends_acknowledged=True),
        )
        assert any("escalation_email" in p for p in validate())

    def test_an_unknown_preset_is_reported(self, configured, monkeypatch):
        monkeypatch.setattr(
            hs, "EMAIL", EmailSettings(enabled=True, preset="notaprovider")
        )
        assert any("unknown" in p for p in validate())

    def test_a_missing_host_without_a_preset_is_reported(
        self, configured, monkeypatch
    ):
        monkeypatch.setattr(
            hs, "EMAIL", EmailSettings(enabled=True, address="a@b.co", smtp_password="p")
        )
        assert any("smtp_host is required" in p for p in validate())

    def test_both_tls_modes_at_once_is_reported(self, configured, monkeypatch):
        monkeypatch.setattr(
            hs,
            "EMAIL",
            EmailSettings(
                enabled=True,
                address="a@b.co",
                smtp_host="mail.b.co",
                use_ssl=True,
                use_starttls=True,
                smtp_password="p",
            ),
        )
        assert any("only one of" in p for p in validate())

    def test_a_malformed_address_is_reported(self, configured, monkeypatch):
        monkeypatch.setattr(
            hs,
            "EMAIL",
            EmailSettings(
                enabled=True,
                address="not-an-address",
                preset="google",
                smtp_password="p",
            ),
        )
        assert any("is not an email address" in p for p in validate())

    def test_a_missing_mailbox_password_is_reported(
        self, configured, monkeypatch
    ):
        monkeypatch.setattr(
            hs,
            "EMAIL",
            EmailSettings(enabled=True, address="a@b.co", preset="google"),
        )
        monkeypatch.delenv("SMTP_PASSWORD", raising=False)
        assert any("smtp_password is required" in p for p in validate())

    def test_the_openai_family_requires_a_model(self, monkeypatch):
        monkeypatch.setattr(
            hs, "LLM", LlmSettings(provider="openai", model="", api_key="k")
        )
        assert any("model is required" in p for p in validate())

    def test_azure_requires_its_endpoint(self, monkeypatch):
        monkeypatch.setattr(
            hs,
            "LLM",
            LlmSettings(provider="azure", model="deploy", api_key="k"),
        )
        assert any("base_url is required" in p for p in validate())

    def test_an_unrecognised_provider_is_reported(self, monkeypatch):
        monkeypatch.setattr(
            hs, "LLM", LlmSettings(provider="mystery", model="m", api_key="k")
        )
        assert any("not recognised" in p for p in validate())

    def test_disabled_provider_needs_no_credential(self, monkeypatch):
        monkeypatch.setattr(hs, "LLM", LlmSettings(provider="disabled"))
        assert not any("credential" in p for p in validate())


# ---------------------------------------------------------------------------
# environment()
# ---------------------------------------------------------------------------


class TestEnvironment:
    def test_the_llm_block_becomes_the_agent_variables(self, configured):
        env = environment()
        assert env["AGENT_LLM_PROVIDER"] == "openai"
        assert env["AGENT_LLM_MODEL"] == "gpt-4o-mini"
        assert env["AGENT_LLM_API_KEY"] == "sk-test"

    def test_the_model_reaches_the_legacy_decision_knob_too(self, configured):
        # Both layers read a model id; leaving one unset would let them drift.
        assert environment()["AGENT_DECISION_MODEL"] == "gpt-4o-mini"

    def test_a_preset_resolves_to_concrete_coordinates(self, configured):
        env = environment()
        assert env["SMTP_HOST"] == "smtp.office365.com"
        assert env["SMTP_PORT"] == "587"
        assert env["SMTP_USE_TLS"] == "1"
        assert env["SMTP_USE_SSL"] == "0"

    def test_an_explicit_host_beats_the_preset(self, monkeypatch):
        monkeypatch.setattr(
            hs,
            "EMAIL",
            EmailSettings(
                enabled=True,
                address="a@b.co",
                preset="google",
                smtp_host="mail.custom.co",
                smtp_port=2525,
                smtp_password="p",
            ),
        )
        env = environment()
        assert env["SMTP_HOST"] == "mail.custom.co"
        assert env["SMTP_PORT"] == "2525"

    def test_a_disabled_mailbox_emits_no_mailbox_variables(self):
        env = environment()
        assert "SMTP_HOST" not in env
        assert "EMAIL_FROM" not in env

    def test_the_display_name_is_emitted_so_patients_see_the_clinic(
        self, configured
    ):
        assert environment()["EMAIL_FROM_NAME"] == "BrightSmile Dental"

    def test_a_blank_display_name_is_omitted_rather_than_empty(self, configured, monkeypatch):
        monkeypatch.setattr(
            hs,
            "EMAIL",
            EmailSettings(
                enabled=True,
                address="a@b.co",
                preset="google",
                smtp_password="p",
            ),
        )
        assert "EMAIL_FROM_NAME" not in environment()

    def test_the_username_falls_back_to_the_address(self, configured):
        # Most providers require a username even when it repeats the address.
        assert environment()["SMTP_USERNAME"] == "reminders@clinic.example"

    def test_the_escalation_address_is_emitted(self, configured):
        assert environment()["AGENT_ESCALATION_EMAIL"] == "staff@clinic.example"

    def test_policy_knobs_are_emitted(self, configured):
        env = environment()
        assert env["AGENT_MAX_REMINDERS_BEFORE_ESCALATION"] == "3"
        assert env["AGENT_REMINDER_INTERVAL_DAYS"] == "7"

    def test_no_value_is_ever_an_empty_string(self, configured):
        # An empty string would overwrite a variable set elsewhere.
        assert "" not in environment().values()

    def test_extra_headers_are_emitted_as_json(self, monkeypatch):
        monkeypatch.setattr(
            hs,
            "LLM",
            LlmSettings(
                provider="openai",
                model="m",
                api_key="k",
                extra_headers={"X-Gateway": "g"},
            ),
        )
        assert '"X-Gateway"' in environment()["AGENT_LLM_EXTRA_HEADERS"]


# ---------------------------------------------------------------------------
# environment() must feed the code that reads it
# ---------------------------------------------------------------------------


class TestEnvironmentIsActuallyConsumed:
    def test_the_emitted_llm_variables_configure_the_provider_layer(
        self, configured
    ):
        from tools.llm_providers import LlmProviderConfig

        config = LlmProviderConfig.from_env(environment())
        assert config.kind == "openai"
        assert config.model == "gpt-4o-mini"
        assert config.api_key == "sk-test"

    def test_the_emitted_mailbox_variables_configure_the_messaging_layer(
        self, configured
    ):
        from tools.config import MessagingConfig

        config = MessagingConfig.from_env(environment())
        assert config.smtp_host == "smtp.office365.com"
        assert config.smtp_port == 587
        assert config.email_from == "reminders@clinic.example"

    def test_the_emitted_display_name_reaches_the_from_header(self, configured):
        from tools.config import MessagingConfig
        from tools.providers import SmtpEmailProvider

        config = MessagingConfig.from_env(environment())
        assert config.email_from_name == "BrightSmile Dental"
        message = _dummy_request_message(SmtpEmailProvider, config)
        assert message["From"] == "BrightSmile Dental <reminders@clinic.example>"

    def test_the_emitted_policy_knobs_configure_the_policy(self, configured):
        from core.config import ClinicPolicyConfig

        env = environment()
        env["AGENT_MAX_REMINDERS_BEFORE_ESCALATION"] = "5"
        env["AGENT_REMINDER_INTERVAL_DAYS"] = "2"
        policy = ClinicPolicyConfig.from_env(env)
        assert policy.max_reminders_before_escalation == 5
        assert policy.reminder_interval_days == 2


def _dummy_request_message(provider_cls, config):
    """Build the From header for one request, without connecting anywhere."""
    from tools.providers import SendRequest

    provider = provider_cls(config)
    request = SendRequest(
        to="patient@example.com", subject="s", body="b"
    )
    return provider._build_message(request)


# ---------------------------------------------------------------------------
# apply()
# ---------------------------------------------------------------------------


class TestApply:
    def test_it_writes_the_variables_into_the_environment(
        self, configured, monkeypatch
    ):
        for key in list(environment()):
            monkeypatch.delenv(key, raising=False)
        applied = hs.apply()
        assert applied == len(environment())
        import os

        assert os.environ["AGENT_LLM_PROVIDER"] == "openai"

    def test_existing_variables_win_when_overriding_is_off(
        self, configured, monkeypatch
    ):
        # This is the vault-injection path: a real secret must not be replaced
        # by a blank in the file.
        monkeypatch.setenv("AGENT_LLM_API_KEY", "from-the-vault")
        hs.apply(override=False)
        import os

        assert os.environ["AGENT_LLM_API_KEY"] == "from-the-vault"

    def test_overriding_replaces_an_existing_variable(self, configured, monkeypatch):
        monkeypatch.setenv("AGENT_LLM_API_KEY", "stale")
        hs.apply(override=True)
        import os

        assert os.environ["AGENT_LLM_API_KEY"] == "sk-test"


# ---------------------------------------------------------------------------
# SMTP presets
# ---------------------------------------------------------------------------


class TestSmtpPresets:
    def test_every_preset_uses_one_encryption_mode(self):
        for name, (host, port, starttls, ssl) in SMTP_PRESETS.items():
            assert not (starttls and ssl), name
            assert starttls or ssl, name
            assert host and port, name

    def test_implicit_tls_presets_use_port_465(self):
        for name, (_host, port, starttls, ssl) in SMTP_PRESETS.items():
            if ssl:
                assert port == 465, name

    def test_starttls_presets_use_port_587(self):
        for name, (_host, _port, starttls, _ssl) in SMTP_PRESETS.items():
            if starttls:
                assert _port == 587, name

    def test_the_documented_presets_all_exist(self):
        for name in (
            "microsoft365",
            "google",
            "exmail",
            "aliyun",
            "zoho",
            "workmail",
            "ses",
            "fastmail",
        ):
            assert name in SMTP_PRESETS

    def test_a_preset_name_is_matched_case_insensitively(self, monkeypatch):
        monkeypatch.setattr(
            hs,
            "EMAIL",
            EmailSettings(enabled=True, address="a@b.co", preset="GOOGLE"),
        )
        assert environment()["SMTP_HOST"] == "smtp.gmail.com"


# ---------------------------------------------------------------------------
# summary() must be safe to paste into a support ticket
# ---------------------------------------------------------------------------


class TestSummary:
    def test_it_never_reveals_a_secret(self, configured):
        import json

        rendered = json.dumps(hs.summary())
        assert "sk-test" not in rendered
        assert "app-password" not in rendered

    def test_it_reports_that_a_credential_is_present(self, configured):
        assert hs.summary()["llm"]["credential"] == "set"
        assert hs.summary()["email"]["credential"] == "set"

    def test_it_reports_the_live_sending_state(self, configured):
        assert hs.summary()["agent"]["live_sending"] is True

    def test_live_sending_is_false_when_only_one_switch_is_set(self, monkeypatch):
        monkeypatch.setattr(hs, "AGENT", AgentSettings(enable_live_sending=True))
        assert hs.summary()["agent"]["live_sending"] is False


class TestFormatFrom:
    def test_it_shows_the_clinic_name_to_patients(self, monkeypatch):
        monkeypatch.setattr(
            hs,
            "EMAIL",
            EmailSettings(enabled=True, address="a@clinic.co", display_name="Clinic"),
        )
        assert hs._format_from() == "Clinic <a@clinic.co>"

    def test_it_falls_back_to_the_bare_address(self, monkeypatch):
        monkeypatch.setattr(
            hs, "EMAIL", EmailSettings(enabled=True, address="a@clinic.co")
        )
        assert hs._format_from() == "a@clinic.co"

    def test_it_says_so_when_nothing_is_set(self):
        assert hs._format_from() == "(not set)"


# ---------------------------------------------------------------------------
# check_* must never raise, because they are the diagnosis path
# ---------------------------------------------------------------------------


class TestChecksNeverRaise:
    def test_check_email_fails_cleanly_when_the_mailbox_is_disabled(self):
        ok, message = hs.check_email()
        assert ok is False
        assert "enabled" in message

    def test_check_llm_reports_a_missing_credential_rather_than_an_sdk_error(self):
        # "install the anthropic package" would be the wrong advice for a
        # clinic that simply has not pasted a key.
        ok, message = hs.check_llm()
        assert ok is False
        assert "credential" in message.lower()

    def test_check_llm_treats_a_disabled_provider_as_success(self, monkeypatch):
        monkeypatch.setattr(hs, "LLM", LlmSettings(provider="disabled"))
        ok, message = hs.check_llm()
        assert ok is True
        assert "rule engine" in message

    def test_check_email_reports_a_missing_password_cleanly(
        self, monkeypatch
    ):
        monkeypatch.setattr(
            hs,
            "EMAIL",
            EmailSettings(enabled=True, address="a@b.co", preset="google"),
        )
        monkeypatch.delenv("SMTP_PASSWORD", raising=False)
        # A missing password must be an error message, not an exception.
        try:
            ok, message = hs.check_email()
        except Exception as exc:  # pragma: no cover - the failure being guarded
            raise AssertionError(f"check_email raised {exc!r}")
        assert ok is False
        assert message


# ---------------------------------------------------------------------------
# .env loading: the file's own recommended setup must work
# ---------------------------------------------------------------------------


class TestDotenvLoading:
    def test_load_dotenv_reads_the_project_env_file(self, monkeypatch):
        calls = []
        import tools.config as tools_config

        monkeypatch.setattr(
            tools_config, "load_env_file", lambda *a, **k: calls.append(a) or 0
        )
        hs.load_dotenv()
        assert calls, "load_dotenv must read the .env file"

    def test_the_mailbox_check_loads_the_env_file(self, monkeypatch):
        # The guide tells clinics to export SMTP_PASSWORD rather than paste it
        # into the file, so the *check* has to read it. Asserting on
        # check_email (not validate, which stays a pure inspection) is the
        # honest contract, and the SMTP connection is faked out.
        monkeypatch.setattr(
            hs,
            "EMAIL",
            EmailSettings(enabled=True, address="a@b.co", preset="google"),
        )
        monkeypatch.setattr(hs, "load_dotenv", _export_password)

        import smtplib

        class FakeSmtp:
            def __init__(self, *args, **kwargs):
                pass

            def ehlo(self):
                pass

            def starttls(self, context=None):
                pass

            def login(self, user, password):
                assert password, "login must receive the exported password"

            def quit(self):
                pass

        monkeypatch.setattr(smtplib, "SMTP", FakeSmtp)
        ok, message = hs.check_email()
        assert ok is True, message

    def test_the_check_entry_points_load_the_env_file(self, monkeypatch):
        calls = []
        monkeypatch.setattr(hs, "load_dotenv", lambda: calls.append(1))
        hs.main(["--show"])
        assert calls, "main() must load .env before reading the environment"


def _export_password():
    """Stand in for load_dotenv(), exporting an app password."""
    os.environ["SMTP_PASSWORD"] = "an-app-password"


# ---------------------------------------------------------------------------
# --send-test must actually transmit
# ---------------------------------------------------------------------------


class TestSendTestEmail:
    def test_it_bypasses_the_simulation_switch(self, monkeypatch):
        """
        Regression: ``apply()`` writes ``MESSAGING_DRY_RUN`` from the AGENT
        block, so the deliberate override has to happen *after* it. When the
        order was wrong, ``--send-test`` reported a PASS while the tool
        simulated the send -- a false proof of delivery.
        """
        monkeypatch.setattr(
            hs,
            "EMAIL",
            EmailSettings(
                enabled=True,
                address="reminders@clinic.example",
                preset="google",
                smtp_password="p",
            ),
        )
        # Live sending stays OFF, so apply() would turn the simulation on.
        monkeypatch.setattr(hs, "AGENT", AgentSettings())
        monkeypatch.setattr(hs, "load_dotenv", _export_password)

        captured = {}
        import tools.messaging as messaging

        def fake_build_tool_registry(config=None, **kwargs):
            captured["config"] = config
            raise RuntimeError("stop before the network")

        monkeypatch.setattr(messaging, "build_tool_registry", fake_build_tool_registry)

        ok, _message = hs.send_test_email("someone@example.com")

        assert ok is False  # the deliberate stop
        assert captured["config"] is not None
        assert captured["config"].simulates is False

    def test_it_never_raises(self, monkeypatch):
        monkeypatch.setattr(hs, "load_dotenv", _export_password)
        try:
            hs.send_test_email("not-an-address")
        except Exception as exc:  # pragma: no cover - the failure being guarded
            raise AssertionError(f"send_test_email raised {exc!r}")


class TestTheInterfaceIsImportable:
    """The README documents this file as a Python interface; keep both in step."""

    def test_it_exposes_exactly_the_documented_functions(self):
        for name in (
            "environment",
            "apply",
            "validate",
            "summary",
            "check_llm",
            "check_email",
            "send_test_email",
        ):
            assert callable(getattr(hs, name)), name

    def test_the_documented_signatures_are_stable(self):
        # README's function table quotes these; a change there is a doc change.
        assert str(inspect.signature(hs.apply)) == "(*, override: 'bool' = True) -> 'int'"
        assert str(inspect.signature(hs.send_test_email)).startswith(
            "(recipient: 'str')"
        )

    def test_environment_renders_without_touching_os_environ(self, monkeypatch):
        monkeypatch.setenv("AGENT_LLM_PROVIDER", "sentinel-should-not-be-read")
        before = dict(os.environ)
        rendered = hs.environment()
        assert dict(os.environ) == before
        assert isinstance(rendered, dict)
        assert rendered["AGENT_LLM_PROVIDER"]  # rendered from the settings

    def test_the_documented_alias_collapse_holds(self):
        # README claims these all mean "openai", and that a typo means anthropic.
        from tools.llm_providers import LlmProviderConfig

        for alias in ("openai-compatible", "ollama", "vllm", "deepseek", "qwen"):
            config = LlmProviderConfig.from_env({"AGENT_LLM_PROVIDER": alias})
            assert config.kind == "openai", alias

        # Matching is case-insensitive, as the README states.
        assert LlmProviderConfig.from_env(
            {"AGENT_LLM_PROVIDER": "OpenAI"}
        ).kind == "openai"
        assert LlmProviderConfig.from_env(
            {"AGENT_LLM_PROVIDER": "AZURE"}
        ).kind == "azure"

        assert LlmProviderConfig.from_env(
            {"AGENT_LLM_PROVIDER": "typo-nonsense"}
        ).kind == "anthropic"

    def test_applying_a_config_reaches_the_agents_own_config_objects(self, monkeypatch):
        # The README's headline claim: apply() is what the agent actually reads.
        from tools.config import MessagingConfig
        from tools.llm_providers import LlmProviderConfig

        monkeypatch.setattr(hs, "LLM", LlmSettings(
            provider="openai", base_url="http://10.0.0.7:8000/v1",
            model="Qwen/Qwen2.5-72B-Instruct",
        ))
        monkeypatch.setattr(hs, "EMAIL", EmailSettings(
            enabled=True, address="reminders@hospital.example",
            display_name="General Hospital", preset="microsoft365",
        ))

        assert hs.apply() > 0

        llm = LlmProviderConfig.from_env()
        assert (llm.kind, llm.base_url, llm.model) == (
            "openai", "http://10.0.0.7:8000/v1", "Qwen/Qwen2.5-72B-Instruct",
        )
        mail = MessagingConfig.from_env()
        assert mail.email_from == "reminders@hospital.example"
        assert mail.email_from_name == "General Hospital"
        assert mail.smtp_host == "smtp.office365.com"
