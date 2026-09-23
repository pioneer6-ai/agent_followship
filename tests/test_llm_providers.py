"""
Tests for the pluggable LLM provider layer (``tools/llm_providers.py``).

The point of this module is that a clinic may bring a vendor other than
Anthropic. Everything here runs offline: the OpenAI-compatible client's HTTP
call goes through the scripted transport, so no key and no network are needed.

The translation tests are the important ones. Anthropic and OpenAI disagree
about where tool results live, and getting that wrong does not fail the first
turn -- it fails the *second* turn of any tool loop, which is exactly the case a
hand-written smoke test tends to miss.
"""

import json

import pytest

from tools.llm_providers import (
    LlmProviderConfig,
    LlmProviderError,
    MissingLlmSdkError,
    OpenAiCompatibleClient,
    create_llm_client,
    describe_llm_setup,
    to_anthropic_response,
    to_openai_messages,
    to_openai_tool_choice,
    to_openai_tools,
)
from tools.transport import FakeTransport, HttpResponse


def make_config(**overrides):
    """An OpenAI-compatible config with a key, so nothing is skipped."""
    values = {
        "kind": "openai",
        "model": "gpt-4o-mini",
        "api_key": "test-key",
    }
    values.update(overrides)
    return LlmProviderConfig(**values)


def chat_reply(content="hello", finish_reason="stop", tool_calls=None):
    """A minimal OpenAI chat-completions response body."""
    message = {"role": "assistant", "content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    return {
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}]
    }


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


class TestConfigFromEnv:
    def test_defaults_to_anthropic_when_provider_unset(self):
        config = LlmProviderConfig.from_env({})
        assert config.kind == "anthropic"
        assert config.model == "claude-sonnet-4-5"

    def test_reads_a_neutral_provider_and_model(self):
        config = LlmProviderConfig.from_env(
            {"AGENT_LLM_PROVIDER": "openai", "AGENT_LLM_MODEL": "gpt-4o"}
        )
        assert config.kind == "openai"
        assert config.model == "gpt-4o"

    def test_disabled_aliases_all_mean_rules_only(self):
        for raw in ("disabled", "none", "off", "rules", "DISABLED", " off "):
            config = LlmProviderConfig.from_env({"AGENT_LLM_PROVIDER": raw})
            assert config.is_disabled, raw

    def test_an_unknown_provider_degrades_to_anthropic_not_an_error(self):
        # A typo must not be able to stop the agent from running at all.
        config = LlmProviderConfig.from_env({"AGENT_LLM_PROVIDER": "anhtropic"})
        assert config.kind == "anthropic"

    def test_vendor_default_model_fills_in_per_kind(self):
        assert LlmProviderConfig.from_env(
            {"AGENT_LLM_PROVIDER": "openai"}
        ).model == "gpt-4o-mini"
        # Azure needs the clinic's own deployment name, so there is no default.
        assert LlmProviderConfig.from_env({"AGENT_LLM_PROVIDER": "azure"}).model == ""

    def test_legacy_decision_model_is_honoured_as_a_fallback(self):
        config = LlmProviderConfig.from_env({"AGENT_DECISION_MODEL": "legacy-model"})
        assert config.model == "legacy-model"

    def test_explicit_model_beats_the_legacy_alias(self):
        config = LlmProviderConfig.from_env(
            {"AGENT_LLM_MODEL": "new", "AGENT_DECISION_MODEL": "old"}
        )
        assert config.model == "new"

    def test_neutral_api_key_name_wins(self):
        config = LlmProviderConfig.from_env(
            {"AGENT_LLM_API_KEY": "neutral", "OPENAI_API_KEY": "vendor"}
        )
        assert config.api_key == "neutral"

    def test_vendor_keys_are_fallbacks_so_an_existing_export_still_works(self):
        assert LlmProviderConfig.from_env(
            {"AGENT_LLM_PROVIDER": "openai", "OPENAI_API_KEY": "vendor"}
        ).api_key == "vendor"
        assert LlmProviderConfig.from_env(
            {"ANTHROPIC_API_KEY": "vendor"}
        ).api_key == "vendor"

    def test_vendor_key_is_not_crossed_between_vendors(self):
        # ANTHROPIC_API_KEY must not be handed to an OpenAI endpoint.
        config = LlmProviderConfig.from_env(
            {"AGENT_LLM_PROVIDER": "openai", "ANTHROPIC_API_KEY": "wrong-vendor"}
        )
        assert config.api_key is None

    def test_blank_values_do_not_become_empty_strings(self):
        config = LlmProviderConfig.from_env(
            {"AGENT_LLM_BASE_URL": "   ", "AGENT_LLM_API_KEY": "  "}
        )
        assert config.base_url is None
        assert config.api_key is None

    def test_numeric_settings_are_parsed(self):
        config = LlmProviderConfig.from_env(
            {"AGENT_LLM_TIMEOUT_SECONDS": "12.5", "AGENT_LLM_MAX_TOKENS": "256"}
        )
        assert config.timeout_seconds == 12.5
        assert config.max_tokens == 256

    def test_a_garbage_number_falls_back_to_the_default(self):
        config = LlmProviderConfig.from_env(
            {"AGENT_LLM_TIMEOUT_SECONDS": "soon", "AGENT_LLM_MAX_TOKENS": "lots"}
        )
        assert config.timeout_seconds == 60.0
        assert config.max_tokens == 1024

    def test_extra_headers_are_parsed_from_json(self):
        config = LlmProviderConfig.from_env(
            {"AGENT_LLM_EXTRA_HEADERS": '{"X-Gateway-Key": "abc"}'}
        )
        assert config.extra_headers == {"X-Gateway-Key": "abc"}

    def test_malformed_extra_headers_do_not_raise(self):
        # A bad header blob must not stop the clinic from running.
        config = LlmProviderConfig.from_env({"AGENT_LLM_EXTRA_HEADERS": "{not json"})
        assert config.extra_headers == {}

    def test_api_key_is_mutable_so_callers_can_override_it(self):
        config = make_config(api_key=None)
        config.api_key = "late-key"
        assert config.api_key == "late-key"


# ---------------------------------------------------------------------------
# Tool declaration translation
# ---------------------------------------------------------------------------


class TestToolTranslation:
    def test_input_schema_becomes_function_parameters(self):
        converted = to_openai_tools(
            [
                {
                    "name": "send_sms",
                    "description": "Send an SMS",
                    "input_schema": {"type": "object", "properties": {}},
                }
            ]
        )
        assert converted == [
            {
                "type": "function",
                "function": {
                    "name": "send_sms",
                    "description": "Send an SMS",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]

    def test_a_tool_without_a_schema_still_declares_an_object(self):
        converted = to_openai_tools([{"name": "noargs"}])
        assert converted[0]["function"]["parameters"] == {"type": "object"}

    def test_empty_tool_list_stays_empty(self):
        assert to_openai_tools([]) == []


class TestToolChoiceTranslation:
    def test_none_is_omitted_rather_than_sent(self):
        assert to_openai_tool_choice(None) is None

    def test_a_plain_string_passes_through(self):
        assert to_openai_tool_choice("auto") == "auto"

    def test_named_tool_becomes_a_function_choice(self):
        assert to_openai_tool_choice({"type": "tool", "name": "send_sms"}) == {
            "type": "function",
            "function": {"name": "send_sms"},
        }

    def test_any_becomes_required(self):
        assert to_openai_tool_choice({"type": "any"}) == "required"

    def test_auto_object_becomes_the_string(self):
        assert to_openai_tool_choice({"type": "auto"}) == "auto"

    def test_an_already_openai_shaped_choice_passes_through(self):
        choice = {"type": "function", "function": {"name": "x"}}
        assert to_openai_tool_choice(choice) == choice

    def test_an_unrecognised_choice_is_dropped(self):
        assert to_openai_tool_choice({"type": "nonsense"}) is None


# ---------------------------------------------------------------------------
# Transcript translation -- the part that breaks multi-turn tool loops
# ---------------------------------------------------------------------------


class TestMessageTranslation:
    def test_system_becomes_a_leading_system_message(self):
        messages = to_openai_messages("You are a helper", [])
        assert messages == [{"role": "system", "content": "You are a helper"}]

    def test_no_system_means_no_system_message(self):
        assert to_openai_messages(None, []) == []

    def test_plain_string_content_passes_through(self):
        messages = to_openai_messages(None, [{"role": "user", "content": "hi"}])
        assert messages == [{"role": "user", "content": "hi"}]

    def test_text_blocks_are_concatenated(self):
        messages = to_openai_messages(
            None,
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "first"},
                        {"type": "text", "text": "second"},
                    ],
                }
            ],
        )
        assert messages == [{"role": "user", "content": "first\nsecond"}]

    def test_tool_use_becomes_tool_calls_with_json_string_arguments(self):
        messages = to_openai_messages(
            None,
            [
                {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "Checking"},
                        {
                            "type": "tool_use",
                            "id": "call_1",
                            "name": "send_sms",
                            "input": {"phone_number": "+6583536885"},
                        },
                    ],
                }
            ],
        )
        assert messages[0]["content"] == "Checking"
        assert messages[0]["tool_calls"] == [
            {
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": "send_sms",
                    "arguments": json.dumps({"phone_number": "+6583536885"}),
                },
            }
        ]

    def test_tool_result_blocks_become_dedicated_tool_messages(self):
        # This is the shape that makes the *second* turn work. OpenAI wants one
        # role="tool" message per call, not a user message holding blocks.
        messages = to_openai_messages(
            None,
            [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "call_1",
                            "content": "sent",
                        }
                    ],
                }
            ],
        )
        assert messages == [
            {"role": "tool", "tool_call_id": "call_1", "content": "sent"}
        ]

    def test_several_tool_results_become_several_tool_messages(self):
        messages = to_openai_messages(
            None,
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "a", "content": "1"},
                        {"type": "tool_result", "tool_use_id": "b", "content": "2"},
                    ],
                }
            ],
        )
        assert [m["tool_call_id"] for m in messages] == ["a", "b"]
        assert all(m["role"] == "tool" for m in messages)

    def test_a_structured_tool_result_is_json_encoded(self):
        messages = to_openai_messages(
            None,
            [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "a",
                            "content": {"status": "sent"},
                        }
                    ],
                }
            ],
        )
        assert json.loads(messages[0]["content"]) == {"status": "sent"}

    def test_an_empty_assistant_turn_becomes_empty_content_not_a_crash(self):
        messages = to_openai_messages(
            None, [{"role": "assistant", "content": []}]
        )
        assert messages == [{"role": "assistant", "content": ""}]

    def test_a_full_two_turn_tool_loop_round_trips(self):
        transcript = [
            {"role": "user", "content": "Remind this patient."},
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "call_1",
                        "name": "send_sms",
                        "input": {"phone_number": "+6583536885"},
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "call_1",
                        "content": '{"status": "sent"}',
                    }
                ],
            },
        ]
        messages = to_openai_messages("Be careful", transcript)
        assert [m["role"] for m in messages] == [
            "system",
            "user",
            "assistant",
            "tool",
        ]
        assert messages[2]["tool_calls"][0]["id"] == "call_1"
        assert messages[3]["tool_call_id"] == "call_1"


# ---------------------------------------------------------------------------
# Response translation
# ---------------------------------------------------------------------------


class TestResponseTranslation:
    def test_plain_text_becomes_a_text_block(self):
        response = to_anthropic_response(chat_reply("done"))
        assert response["content"] == [{"type": "text", "text": "done"}]
        assert response["stop_reason"] == "end_turn"

    def test_a_tool_call_becomes_a_tool_use_block_with_parsed_input(self):
        body = chat_reply(
            content=None,
            finish_reason="tool_calls",
            tool_calls=[
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "send_sms",
                        "arguments": '{"phone_number": "+6583536885", "message": "hi"}',
                    },
                }
            ],
        )
        response = to_anthropic_response(body)
        assert response["stop_reason"] == "tool_use"
        block = response["content"][0]
        assert block["type"] == "tool_use"
        assert block["name"] == "send_sms"
        assert block["input"] == {"phone_number": "+6583536885", "message": "hi"}

    def test_text_and_a_tool_call_can_coexist(self):
        body = chat_reply(
            content="Let me check",
            finish_reason="tool_calls",
            tool_calls=[
                {
                    "id": "c1",
                    "function": {"name": "send_sms", "arguments": "{}"},
                }
            ],
        )
        response = to_anthropic_response(body)
        assert [b["type"] for b in response["content"]] == ["text", "tool_use"]

    def test_malformed_arguments_degrade_to_an_empty_input(self):
        # Returning {} lets the schema layer reject the call with a real
        # message, which is more useful than a JSON traceback.
        body = chat_reply(
            content=None,
            tool_calls=[
                {"id": "c1", "function": {"name": "send_sms", "arguments": "{oops"}}
            ],
        )
        assert to_anthropic_response(body)["content"][0]["input"] == {}

    def test_absent_arguments_degrade_to_an_empty_input(self):
        body = chat_reply(
            content=None, tool_calls=[{"id": "c1", "function": {"name": "x"}}]
        )
        assert to_anthropic_response(body)["content"][0]["input"] == {}

    def test_an_empty_reply_is_well_formed_rather_than_an_exception(self):
        # The caller's guardrail should decide what to do, not a crash here.
        response = to_anthropic_response(chat_reply(content=None))
        assert response["content"] == []

    def test_an_already_parsed_object_as_arguments_is_accepted(self):
        body = chat_reply(
            content=None,
            tool_calls=[
                {
                    "id": "c1",
                    "function": {"name": "x", "arguments": {"a": 1}},
                }
            ],
        )
        assert to_anthropic_response(body)["content"][0]["input"] == {"a": 1}

    def test_finish_reason_length_maps_to_max_tokens(self):
        assert (
            to_anthropic_response(chat_reply(finish_reason="length"))["stop_reason"]
            == "max_tokens"
        )

    def test_an_unknown_finish_reason_becomes_end_turn(self):
        assert (
            to_anthropic_response(chat_reply(finish_reason="who_knows"))[
                "stop_reason"
            ]
            == "end_turn"
        )

    def test_a_tool_call_without_an_id_gets_a_generated_one(self):
        body = chat_reply(
            content=None, tool_calls=[{"function": {"name": "x", "arguments": "{}"}}]
        )
        assert to_anthropic_response(body)["content"][0]["id"] == "call_0"

    def test_no_choices_raises_a_provider_error(self):
        # Answering but not being a chat API is a real failure, not emptiness.
        with pytest.raises(LlmProviderError, match="no completion choices"):
            to_anthropic_response({"message": "not a completion"})

    def test_an_error_body_is_included_in_the_provider_error(self):
        with pytest.raises(LlmProviderError, match="quota exceeded"):
            to_anthropic_response({"error": "quota exceeded"})


# ---------------------------------------------------------------------------
# URL and header construction
# ---------------------------------------------------------------------------


class TestEndpointConstruction:
    def test_openai_uses_the_conventional_path(self):
        assert OpenAiCompatibleClient(
            make_config(base_url="https://api.example.com/v1")
        ).url() == "https://api.example.com/v1/chat/completions"

    def test_a_url_already_ending_in_the_path_is_not_doubled(self):
        client = OpenAiCompatibleClient(
            make_config(base_url="https://api.example.com/v1/chat/completions")
        )
        assert client.url() == "https://api.example.com/v1/chat/completions"

    def test_a_self_hosted_base_url_is_respected(self):
        # Ollama / vLLM are the reason a clinic with no vendor can still run.
        client = OpenAiCompatibleClient(make_config(base_url="http://localhost:11434/v1"))
        assert client.url() == "http://localhost:11434/v1/chat/completions"

    def test_azure_builds_a_deployment_path_with_the_api_version(self):
        client = OpenAiCompatibleClient(
            make_config(
                kind="azure",
                model="my-deployment",
                base_url="https://res.openai.azure.com",
                api_version="2024-02-01",
            )
        )
        assert client.url() == (
            "https://res.openai.azure.com/openai/deployments/my-deployment"
            "/chat/completions?api-version=2024-02-01"
        )

    def test_azure_defaults_its_api_version(self):
        client = OpenAiCompatibleClient(
            make_config(kind="azure", model="d", base_url="https://r.openai.azure.com")
        )
        assert "api-version=2024-10-21" in client.url()

    def test_a_bearer_token_is_sent_for_openai(self):
        headers = OpenAiCompatibleClient(make_config()).headers()
        assert headers["Authorization"] == "Bearer test-key"

    def test_azure_sends_api_key_header_instead(self):
        headers = OpenAiCompatibleClient(
            make_config(kind="azure", model="d")
        ).headers()
        assert headers["api-key"] == "test-key"
        assert "Authorization" not in headers

    def test_extra_headers_and_organization_are_merged(self):
        headers = OpenAiCompatibleClient(
            make_config(
                organization="org-1", extra_headers={"X-Gateway": "g"}
            )
        ).headers()
        assert headers["OpenAI-Organization"] == "org-1"
        assert headers["X-Gateway"] == "g"

    def test_no_key_means_no_authorization_header(self):
        headers = OpenAiCompatibleClient(make_config(api_key=None)).headers()
        assert "Authorization" not in headers

    def test_describe_names_the_model_and_url(self):
        text = OpenAiCompatibleClient(make_config()).describe()
        assert "gpt-4o-mini" in text and "openai" in text


# ---------------------------------------------------------------------------
# Payload construction
# ---------------------------------------------------------------------------


class TestPayload:
    def test_model_and_max_tokens_come_from_the_config(self):
        payload = OpenAiCompatibleClient(make_config(max_tokens=333)).build_payload()
        assert payload["model"] == "gpt-4o-mini"
        assert payload["max_tokens"] == 333

    def test_per_call_overrides_beat_the_config(self):
        payload = OpenAiCompatibleClient(make_config()).build_payload(
            model="gpt-4o", max_tokens=7
        )
        assert payload["model"] == "gpt-4o"
        assert payload["max_tokens"] == 7

    def test_tools_are_converted_and_absent_when_not_requested(self):
        client = OpenAiCompatibleClient(make_config())
        assert "tools" not in client.build_payload()
        payload = client.build_payload(tools=[{"name": "t"}])
        assert payload["tools"][0]["function"]["name"] == "t"

    def test_tool_choice_is_omitted_when_none(self):
        payload = OpenAiCompatibleClient(make_config()).build_payload(
            messages=[{"role": "user", "content": "hi"}]
        )
        assert "tool_choice" not in payload

    def test_temperature_is_only_included_when_set(self):
        client = OpenAiCompatibleClient(make_config())
        assert "temperature" not in client.build_payload()
        assert client.build_payload(temperature=0.0)["temperature"] == 0.0


# ---------------------------------------------------------------------------
# The live call, over the scripted transport
# ---------------------------------------------------------------------------


class TestCreateCall:
    def test_a_successful_call_returns_anthropic_shaped_content(self):
        transport = FakeTransport([HttpResponse(status_code=200, body=chat_reply("ok"))])
        client = OpenAiCompatibleClient(make_config(), transport=transport)
        response = client.messages.create(messages=[{"role": "user", "content": "hi"}])
        assert response["content"] == [{"type": "text", "text": "ok"}]
        assert client.last_response["choices"][0]["message"]["content"] == "ok"

    def test_the_recorded_request_carries_the_translated_payload(self):
        transport = FakeTransport([HttpResponse(status_code=200, body=chat_reply())])
        client = OpenAiCompatibleClient(make_config(), transport=transport)
        client.messages.create(
            system="sys", messages=[{"role": "user", "content": "hi"}], max_tokens=9
        )
        request = transport.requests[0]
        assert request["payload"]["messages"][0] == {"role": "system", "content": "sys"}
        assert request["payload"]["max_tokens"] == 9

    def test_a_non_2xx_response_raises_with_the_provider_message(self):
        transport = FakeTransport(
            [
                HttpResponse(
                    status_code=429,
                    body={"error": {"message": "rate limited", "code": "429"}},
                )
            ]
        )
        client = OpenAiCompatibleClient(make_config(), transport=transport)
        with pytest.raises(LlmProviderError, match="rate limited"):
            client.messages.create(messages=[])

    def test_a_failure_with_an_unhelpful_body_still_explains_itself(self):
        transport = FakeTransport([HttpResponse(status_code=500, body="boom")])
        client = OpenAiCompatibleClient(make_config(), transport=transport)
        with pytest.raises(LlmProviderError, match="HTTP 500"):
            client.messages.create(messages=[])

    def test_a_transport_level_failure_raises_a_provider_error(self):
        transport = FakeTransport(
            [HttpResponse(status_code=0, error="connection refused")]
        )
        client = OpenAiCompatibleClient(make_config(), transport=transport)
        with pytest.raises(LlmProviderError, match="connection refused"):
            client.messages.create(messages=[])


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


class TestCreateClient:
    def test_disabled_returns_none_rather_than_raising(self):
        # None means "use the rule engine" and is a supported outcome.
        assert create_llm_client(LlmProviderConfig(kind="disabled")) is None

    def test_the_disabled_spelling_from_a_mapping_also_returns_none(self):
        assert create_llm_client(env={"AGENT_LLM_PROVIDER": "off"}) is None

    def test_an_openai_config_yields_the_compatible_client(self):
        client = create_llm_client(make_config())
        assert isinstance(client, OpenAiCompatibleClient)

    def test_an_openai_config_without_a_model_is_an_error(self):
        with pytest.raises(LlmProviderError, match="requires a model id"):
            create_llm_client(make_config(model=""))

    def test_anthropic_without_the_sdk_raises_a_missing_sdk_error(self):
        # The SDK is optional and not installed in the default environment.
        config = LlmProviderConfig(kind="anthropic", model="claude-sonnet-4-5")
        try:
            import anthropic  # noqa: F401

            pytest.skip("anthropic SDK is installed here")
        except ImportError:
            pass
        with pytest.raises(MissingLlmSdkError, match="anthropic"):
            create_llm_client(config)

    def test_a_pass_through_transport_reaches_the_client(self):
        transport = FakeTransport([HttpResponse(status_code=200, body=chat_reply())])
        client = create_llm_client(make_config(), transport=transport)
        client.messages.create(messages=[])
        assert len(transport.requests) == 1


# ---------------------------------------------------------------------------
# Setup description
# ---------------------------------------------------------------------------


class TestDescribeSetup:
    def test_it_never_leaks_the_credential(self):
        described = describe_llm_setup({"AGENT_LLM_API_KEY": "super-secret"})
        assert "super-secret" not in json.dumps(described)

    def test_it_reports_the_resolved_provider(self):
        described = describe_llm_setup({"AGENT_LLM_PROVIDER": "openai"})
        assert described["provider"] == "openai"

    def test_it_reports_a_disabled_setup_as_not_ready(self):
        described = describe_llm_setup({"AGENT_LLM_PROVIDER": "disabled"})
        assert described["ready"] is False
        assert "rule engine" in described["note"]

    def test_a_configured_provider_with_a_credential_is_ready(self):
        described = describe_llm_setup(
            {"AGENT_LLM_PROVIDER": "openai", "AGENT_LLM_API_KEY": "k"}
        )
        assert described["ready"] is True
        assert described["credential_present"] is True

    def test_a_credential_less_setup_is_not_ready(self):
        described = describe_llm_setup({"AGENT_LLM_PROVIDER": "openai"})
        assert described["ready"] is False
        assert described["credential_present"] is False
