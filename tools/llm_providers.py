"""
Pluggable LLM clients, so a clinic can bring its own model.

The agent's decision layer (``agent/decision.py``) and the tool-use loop
(``tools/llm_agent.py``) are both written against one small duck-typed client
interface, the Anthropic Messages API shape::

    client.messages.create(
        model=..., max_tokens=..., system=..., messages=[...], tools=[...]
    ) -> {"content": [{"type": "text"|"tool_use", ...}], "stop_reason": ...}

That shape is a *contract*, not a vendor commitment. This module keeps vendors
behind it:

* :class:`AnthropicMessagesClient` -- the real ``anthropic`` SDK.
* :class:`OpenAiCompatibleClient` -- any ``/chat/completions`` endpoint
  (OpenAI, Azure OpenAI, Ollama, vLLM, TGI, DeepSeek, Qwen, Groq, Together,
  LiteLLM, an in-house gateway, ...). It translates Anthropic-shaped requests
  into OpenAI function-calling requests and translates the replies back, so
  nothing upstream of this module changes when the vendor does.

Both never raise for a *provider* problem: the adapter raises
:class:`LlmProviderError` only on a transport/HTTP failure, which the decision
engine already catches and turns into an ``llm-error`` fallback to the rule
engine. A clinic therefore cannot take the agent down by pointing it at the
wrong endpoint.

Only the Anthropic *branch* imports the ``anthropic`` package, and only lazily,
so the offline demo and the whole test suite run with no SDK and no key.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence
import json
import os

from tools.transport import HttpTransport, UrllibTransport


class LlmProviderError(RuntimeError):
    """A model call failed for a reason outside the caller's control."""


class MissingLlmSdkError(LlmProviderError):
    """The SDK required for the selected provider is not installed."""


#: Provider kinds accepted in ``AGENT_LLM_PROVIDER``.
PROVIDER_ANTHROPIC = "anthropic"
PROVIDER_OPENAI = "openai"
PROVIDER_AZURE = "azure"
PROVIDER_DISABLED = "disabled"

#: Names that all mean "an OpenAI-compatible /chat/completions endpoint".
_OPENAI_ALIASES = {
    "openai",
    "openai_compatible",
    "openai-compatible",
    "compatible",
    "custom",
    "ollama",
    "vllm",
    "tgi",
    "litellm",
    "deepseek",
    "qwen",
    "groq",
    "together",
    "moonshot",
    "zhipu",
}

#: Names that mean "no model -- run the deterministic rule engine only".
_DISABLED_ALIASES = {"disabled", "none", "off", "rules", "rules_only", "rule"}

#: OpenAI ``finish_reason`` -> Anthropic ``stop_reason``.
_STOP_REASONS = {
    "tool_calls": "tool_use",
    "function_call": "tool_use",
    "stop": "end_turn",
    "length": "max_tokens",
    "content_filter": "end_turn",
}


@dataclass
class LlmProviderConfig:
    """
    Everything needed to reach one model endpoint.

    Attributes:
        kind: ``anthropic``, ``openai``, ``azure`` or ``disabled``.
        model: Model id (or Azure deployment name).
        api_key: Credential. Never logged.
        base_url: Endpoint root. Vendor defaults apply when blank.
        api_version: Azure ``api-version`` query value.
        timeout_seconds: Per-request timeout.
        max_tokens: Default completion budget when a caller omits one.
        extra_headers: Extra headers, e.g. a gateway's routing key.
        organization: OpenAI organization/project routing.
    """

    kind: str = PROVIDER_ANTHROPIC
    model: str = ""
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    api_version: Optional[str] = None
    timeout_seconds: float = 60.0
    max_tokens: int = 1024
    extra_headers: Dict[str, str] = field(default_factory=dict)
    organization: Optional[str] = None

    @property
    def is_disabled(self) -> bool:
        """Whether this configuration means "run the rules only"."""
        return self.kind == PROVIDER_DISABLED

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "LlmProviderConfig":
        """
        Read the LLM configuration from environment variables.

        The variable names are deliberately vendor-neutral, so a clinic picks a
        vendor by setting one value rather than by editing code:

        ===========================  =========================================
        ``AGENT_LLM_PROVIDER``       ``anthropic`` | ``openai`` | ``azure`` |
                                     ``disabled``
        ``AGENT_LLM_MODEL``          model id, e.g. ``claude-sonnet-4-5``,
                                     ``gpt-4o-mini``, ``llama3.1:8b``
        ``AGENT_LLM_API_KEY``        credential (falls back to
                                     ``ANTHROPIC_API_KEY`` / ``OPENAI_API_KEY``)
        ``AGENT_LLM_BASE_URL``       endpoint root
        ``AGENT_LLM_API_VERSION``    Azure ``api-version``
        ``AGENT_LLM_TIMEOUT_SECONDS``per-request timeout
        ``AGENT_LLM_MAX_TOKENS``     completion budget
        ``AGENT_DECISION_MODEL``     legacy alias for the model id
        ===========================  =========================================

        Args:
            env: Mapping to read instead of ``os.environ`` (tests).

        Returns:
            A populated :class:`LlmProviderConfig`.
        """
        source: Mapping[str, str] = os.environ if env is None else env
        raw_kind = str(source.get("AGENT_LLM_PROVIDER") or "").strip().lower()
        kind = _normalize_kind(raw_kind)

        model = (
            str(source.get("AGENT_LLM_MODEL") or "").strip()
            or str(source.get("AGENT_DECISION_MODEL") or "").strip()
            or _default_model(kind)
        )

        return cls(
            kind=kind,
            model=model,
            api_key=_resolve_api_key(source, kind),
            base_url=str(source.get("AGENT_LLM_BASE_URL") or "").strip() or None,
            api_version=str(source.get("AGENT_LLM_API_VERSION") or "").strip() or None,
            timeout_seconds=_env_float(source, "AGENT_LLM_TIMEOUT_SECONDS", 60.0),
            max_tokens=_env_int(source, "AGENT_LLM_MAX_TOKENS", 1024),
            extra_headers=_parse_headers(source.get("AGENT_LLM_EXTRA_HEADERS")),
            organization=str(source.get("AGENT_LLM_ORGANIZATION") or "").strip() or None,
        )


def _normalize_kind(raw: str) -> str:
    """Map the many ways a vendor can be named onto a supported kind."""
    if not raw:
        return PROVIDER_ANTHROPIC
    if raw in _DISABLED_ALIASES:
        return PROVIDER_DISABLED
    if raw in ("azure", "azure_openai", "azure-openai"):
        return PROVIDER_AZURE
    if raw in _OPENAI_ALIASES:
        return PROVIDER_OPENAI
    # An unrecognised name is treated as the default rather than an error, so a
    # typo degrades to "the usual vendor" instead of stopping the agent.
    return PROVIDER_ANTHROPIC


def _default_model(kind: str) -> str:
    """Vendor default model id, used when the clinic names none."""
    if kind == PROVIDER_ANTHROPIC:
        return "claude-sonnet-4-5"
    if kind == PROVIDER_AZURE:
        return ""
    return "gpt-4o-mini"


def _resolve_api_key(source: Mapping[str, str], kind: str) -> Optional[str]:
    """
    Find the credential, preferring the neutral name.

    The vendor-specific fallbacks matter because a clinic that already exports
    ``OPENAI_API_KEY`` should not have to rename it.
    """
    for name in ("AGENT_LLM_API_KEY", "LLM_API_KEY"):
        value = str(source.get(name) or "").strip()
        if value:
            return value
    if kind == PROVIDER_ANTHROPIC:
        return str(source.get("ANTHROPIC_API_KEY") or "").strip() or None
    return str(source.get("OPENAI_API_KEY") or "").strip() or None


def _parse_headers(raw: Any) -> Dict[str, str]:
    """Parse ``AGENT_LLM_EXTRA_HEADERS`` as a JSON object of header names."""
    text = str(raw or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except ValueError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {str(key): str(value) for key, value in parsed.items()}


def _env_float(source: Mapping[str, str], name: str, default: float) -> float:
    """Parse a float, falling back on garbage so a typo cannot break startup."""
    try:
        return float(str(source.get(name) or "").strip())
    except ValueError:
        return default


def _env_int(source: Mapping[str, str], name: str, default: int) -> int:
    """Parse an int, falling back on garbage."""
    try:
        return int(str(source.get(name) or "").strip())
    except ValueError:
        return default


class _MessagesResource:
    """The ``client.messages`` namespace both adapters expose."""

    def create(self, **kwargs: Any) -> Dict[str, Any]:  # pragma: no cover - abstract
        """Create a completion. Implemented by subclasses."""
        raise NotImplementedError


class AnthropicMessagesClient:
    """
    Wraps the real ``anthropic`` SDK behind the shared contract.

    Thin by design -- the SDK already speaks this shape -- but wrapping it means
    every provider reaches the agent through the same object, so callers never
    branch on the vendor and tests can substitute any of them.
    """

    def __init__(self, client: Any, *, provider: str = PROVIDER_ANTHROPIC) -> None:
        """
        Args:
            client: An ``anthropic.Anthropic`` instance.
            provider: Kind label, surfaced for diagnostics.
        """
        self._client = client
        self.provider = provider
        self.messages = client.messages

    def describe(self) -> str:
        """One-line description for logs and health checks."""
        return f"anthropic (model={getattr(self._client, '_agent_model', '?')})"


class OpenAiCompatibleClient:
    """
    Speaks the contract on top of any OpenAI-compatible chat-completions API.

    Translation is the whole job:

    * ``system`` becomes a leading ``role: "system"`` message.
    * ``tools[].input_schema`` becomes ``tools[].function.parameters``.
    * ``tool_choice={"type": "tool", "name": n}`` becomes
      ``{"type": "function", "function": {"name": n}}``.
    * An assistant turn containing ``tool_use`` blocks becomes an assistant
      message with ``tool_calls`` (arguments re-serialized to a JSON string).
    * A user turn containing ``tool_result`` blocks becomes one
      ``role: "tool"`` message per result, which is how OpenAI threads results.
    * The reply is converted back into ``content`` blocks, so
      ``normalize_turn`` and ``_extract_tool_input`` work unchanged -- including
      for models that emit no tool call at all.

    A transport (not ``requests``) performs the HTTP call, which keeps the
    project's CA-bundle handling and makes every branch testable offline.
    """

    def __init__(
        self,
        config: LlmProviderConfig,
        *,
        transport: Optional[HttpTransport] = None,
    ) -> None:
        """
        Args:
            config: Endpoint configuration.
            transport: HTTP transport; defaults to the stdlib one.
        """
        self.config = config
        self.transport: HttpTransport = transport or UrllibTransport()
        self.messages = _MessagesResource()
        self.messages.create = self._create  # type: ignore[method-assign]
        self.provider = config.kind
        self.last_response: Optional[Dict[str, Any]] = None

    def describe(self) -> str:
        """One-line description for logs and health checks."""
        return (
            f"{self.config.kind} (model={self.config.model or '?'}, "
            f"url={self.url()})"
        )

    def endpoint_url(self) -> str:
        """Full URL the completion request is posted to."""
        return self.url()

    def url(self) -> str:
        """
        Build the completion URL for this vendor.

        Azure needs its deployment-and-api-version path; everything else is the
        conventional ``/chat/completions`` under ``base_url``.
        """
        base = (self.config.base_url or _default_base_url(self.config.kind)).rstrip("/")
        if self.config.kind == PROVIDER_AZURE:
            if "/chat/completions" in base:
                return base
            url = f"{base}/openai/deployments/{self.config.model}/chat/completions"
            version = self.config.api_version or "2024-10-21"
            return f"{url}?api-version={version}"
        if base.endswith("/chat/completions"):
            return base
        return f"{base}/chat/completions"

    def headers(self) -> Dict[str, str]:
        """Auth and content headers for this vendor."""
        headers: Dict[str, str] = {"Content-Type": "application/json"}
        if self.config.kind == PROVIDER_AZURE:
            if self.config.api_key:
                headers["api-key"] = self.config.api_key
        elif self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        if self.config.organization:
            headers["OpenAI-Organization"] = self.config.organization
        headers.update(self.config.extra_headers)
        return headers

    def _create(self, **kwargs: Any) -> Dict[str, Any]:
        """
        Perform one completion and return an Anthropic-shaped response.

        Raises:
            LlmProviderError: On a transport failure, a non-2xx status, or a
                reply with no usable choice. The decision engine catches this and
                falls back to the rule engine.
        """
        payload = self.build_payload(**kwargs)
        response = self.transport.post_json(
            self.url(),
            headers=self.headers(),
            payload=payload,
            timeout=self.config.timeout_seconds,
        )
        if not response.ok:
            raise LlmProviderError(self._describe_failure(response))
        self.last_response = response.json_body()
        return to_anthropic_response(self.last_response)

    def _describe_failure(self, response: Any) -> str:
        """Turn a failed HTTP response into one actionable sentence."""
        body = response.json_body()
        detail = ""
        error = body.get("error") if isinstance(body, dict) else None
        if isinstance(error, dict):
            detail = str(error.get("message") or error.get("code") or "")
        elif error:
            detail = str(error)
        if not detail:
            detail = str(response.error or response.text() or "")[:300]
        return (
            f"{self.config.kind} model call failed: HTTP {response.status_code} "
            f"{detail}".strip()
        )

    def build_payload(self, **kwargs: Any) -> Dict[str, Any]:
        """
        Translate an Anthropic-shaped request into an OpenAI-shaped payload.

        Kept public and pure so tests can assert the translation without a
        network, and so a clinic can inspect exactly what leaves the building.
        """
        payload: Dict[str, Any] = {
            "model": kwargs.get("model") or self.config.model,
            "messages": to_openai_messages(
                kwargs.get("system"), kwargs.get("messages") or []
            ),
            "max_tokens": int(kwargs.get("max_tokens") or self.config.max_tokens),
        }
        tools = kwargs.get("tools")
        if tools:
            payload["tools"] = to_openai_tools(tools)
        choice = to_openai_tool_choice(kwargs.get("tool_choice"))
        if choice is not None:
            payload["tool_choice"] = choice
        temperature = kwargs.get("temperature")
        if temperature is not None:
            payload["temperature"] = temperature
        return payload


def _default_base_url(kind: str) -> str:
    """Vendor default endpoint root."""
    if kind == PROVIDER_AZURE:
        return ""
    return "https://api.openai.com/v1"


def to_openai_tools(tools: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """
    Convert Anthropic tool declarations into OpenAI function declarations.

    ``input_schema`` and ``parameters`` are the same JSON Schema, so only the
    envelope changes.
    """
    converted: List[Dict[str, Any]] = []
    for tool in tools:
        converted.append(
            {
                "type": "function",
                "function": {
                    "name": tool.get("name"),
                    "description": tool.get("description", ""),
                    "parameters": tool.get("input_schema") or {"type": "object"},
                },
            }
        )
    return converted


def to_openai_tool_choice(choice: Any) -> Optional[Any]:
    """
    Convert ``tool_choice`` to its OpenAI equivalent.

    ``{"type": "tool", "name": n}`` forces function ``n``; ``"auto"``/``"any"``
    map onto themselves; ``None`` stays ``None`` so the field is omitted.
    """
    if choice is None:
        return None
    if isinstance(choice, str):
        return choice
    if isinstance(choice, Mapping):
        if choice.get("type") == "tool" and choice.get("name"):
            return {"type": "function", "function": {"name": choice["name"]}}
        if choice.get("type") == "function":
            return choice
        if choice.get("type") == "auto":
            return "auto"
        if choice.get("type") == "any":
            return "required"
    return None


def to_openai_messages(
    system: Any, messages: Sequence[Any]
) -> List[Dict[str, Any]]:
    """
    Translate an Anthropic transcript into OpenAI chat messages.

    The two shapes disagree mainly on tool results: Anthropic returns them as
    blocks inside a *user* message, OpenAI wants a dedicated ``role: "tool"``
    message per call. Getting this wrong makes the second turn of a tool loop
    fail, so it is handled explicitly and unit-tested.
    """
    converted: List[Dict[str, Any]] = []
    system_text = _flatten_text(system)
    if system_text:
        converted.append({"role": "system", "content": system_text})

    for message in messages or []:
        role = _field(message, "role") or "user"
        content = _field(message, "content")

        if isinstance(content, str):
            converted.append({"role": role, "content": content})
            continue

        blocks = list(content or [])
        tool_results = [b for b in blocks if _field(b, "type") == "tool_result"]
        if tool_results:
            for block in tool_results:
                converted.append(
                    {
                        "role": "tool",
                        "tool_call_id": str(_field(block, "tool_use_id") or ""),
                        "content": _flatten_text(_field(block, "content")),
                    }
                )
            continue

        text = _flatten_text(blocks) if blocks else ""
        entry: Dict[str, Any] = {"role": role, "content": text}
        calls = [_to_openai_tool_call(b) for b in blocks if _field(b, "type") == "tool_use"]
        if calls:
            # The tool call travels in ``tool_calls``. Leaving the serialized
            # ``tool_use`` block in ``content`` as well would feed the model a
            # duplicate of its own call as prose, so content is narrowed to the
            # genuine text when calls are present.
            entry["content"] = _flatten_text(_text_blocks_only(blocks))
            entry["tool_calls"] = calls
        converted.append(entry)

    return converted


def _text_blocks_only(blocks: Sequence[Any]) -> List[Any]:
    """Keep only the blocks that represent real text."""
    kept: List[Any] = []
    for block in blocks:
        if isinstance(block, str):
            kept.append(block)
        elif isinstance(block, Mapping) and block.get("type") == "text":
            kept.append(block)
    return kept


def _to_openai_tool_call(block: Any) -> Dict[str, Any]:
    """Convert one ``tool_use`` block into an OpenAI ``tool_calls`` entry."""
    arguments = _field(block, "input") or {}
    return {
        "id": str(_field(block, "id") or ""),
        "type": "function",
        "function": {
            "name": str(_field(block, "name") or ""),
            "arguments": json.dumps(arguments, ensure_ascii=False),
        },
    }


def _flatten_text(value: Any) -> str:
    """
    Reduce text-or-blocks-or-object into a plain string.

    OpenAI rejects structured content in the positions this adapter uses, so
    everything is flattened rather than passed through.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        if "text" in value:
            return str(value["text"] or "")
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (list, tuple)):
        parts = []
        for item in value:
            if isinstance(item, Mapping) and item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
            elif isinstance(item, Mapping) and "text" in item:
                parts.append(str(item["text"] or ""))
            elif isinstance(item, str):
                parts.append(item)
            elif item is not None:
                parts.append(json.dumps(item, ensure_ascii=False, default=str))
        return "\n".join(part for part in parts if part)
    return str(value)


def _field(obj: Any, key: str, default: Any = None) -> Any:
    """Read ``key`` from a mapping or an attribute, whichever the object is."""
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    return getattr(obj, key, default)


def to_anthropic_response(body: Mapping[str, Any]) -> Dict[str, Any]:
    """
    Convert an OpenAI chat-completions reply into Anthropic-shaped content.

    Returns a plain dict, which the callers already accept (``_extract_tool_input``
    and ``normalize_turn`` both handle dicts as well as SDK objects).

    A reply with neither text nor a tool call still yields a well-formed empty
    response, so the caller's own guardrail -- not a crash -- decides what to do.

    Raises:
        LlmProviderError: When the body contains no choice at all, which means
            the endpoint answered but is not a chat-completions API.
    """
    choices = body.get("choices") if isinstance(body, Mapping) else None
    if not choices:
        message = ""
        if isinstance(body, Mapping) and body.get("error"):
            message = str(body.get("error"))
        raise LlmProviderError(
            "model endpoint returned no completion choices"
            + (f": {message}" if message else "")
        )

    choice = choices[0] or {}
    message = choice.get("message") or {}
    content: List[Dict[str, Any]] = []

    text = _flatten_text(message.get("content"))
    if text:
        content.append({"type": "text", "text": text})

    for index, call in enumerate(message.get("tool_calls") or []):
        if not isinstance(call, Mapping):
            continue
        function = call.get("function") or {}
        content.append(
            {
                "type": "tool_use",
                "id": str(call.get("id") or f"call_{index}"),
                "name": str(function.get("name") or ""),
                "input": _parse_arguments(function.get("arguments")),
            }
        )

    return {
        "content": content,
        "stop_reason": _STOP_REASONS.get(
            str(choice.get("finish_reason") or "stop"), "end_turn"
        ),
    }


def _parse_arguments(raw: Any) -> Dict[str, Any]:
    """
    Parse a tool call's arguments.

    Models occasionally emit malformed or empty JSON. Returning ``{}`` rather
    than raising lets the caller's schema/guardrail layer reject the call with a
    useful message, which is more useful than a parse traceback.
    """
    if isinstance(raw, Mapping):
        return dict(raw)
    text = str(raw or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def create_llm_client(
    config: Optional[LlmProviderConfig] = None,
    *,
    transport: Optional[HttpTransport] = None,
    env: Optional[Mapping[str, str]] = None,
) -> Optional[Any]:
    """
    Build the client for a configuration, or ``None`` when no model is wanted.

    Returning ``None`` is meaningful and not an error: every caller treats it as
    "run the deterministic rule engine", which keeps an unconfigured deployment
    fully functional.

    Args:
        config: Provider configuration; read from the environment when omitted.
        transport: HTTP transport for the OpenAI-compatible path (tests).
        env: Environment mapping to read when ``config`` is omitted.

    Returns:
        A client exposing ``.messages.create``, or ``None``.

    Raises:
        MissingLlmSdkError: When Anthropic is selected but the SDK is absent.
    """
    resolved = config or LlmProviderConfig.from_env(env)

    if resolved.is_disabled:
        return None

    if resolved.kind == PROVIDER_ANTHROPIC:
        return AnthropicMessagesClient(
            _create_anthropic_sdk_client(resolved.api_key)
        )

    if not resolved.model:
        raise LlmProviderError(
            f"{resolved.kind} requires a model id; set AGENT_LLM_MODEL"
        )
    return OpenAiCompatibleClient(resolved, transport=transport)


def _create_anthropic_sdk_client(api_key: Optional[str]) -> Any:
    """Instantiate the real Anthropic SDK client (lazy import)."""
    try:
        import anthropic  # noqa: PLC0415 - intentionally lazy
    except ImportError as exc:
        raise MissingLlmSdkError(
            "The 'anthropic' package is required for the Anthropic provider. "
            "Install it with 'pip install anthropic', or select an "
            "OpenAI-compatible provider with AGENT_LLM_PROVIDER=openai."
        ) from exc
    if api_key:
        return anthropic.Anthropic(api_key=api_key)
    return anthropic.Anthropic()


def describe_llm_setup(
    env: Optional[Mapping[str, str]] = None,
) -> Dict[str, Any]:
    """
    Summarize the LLM configuration without contacting anything.

    Used by the setup tool and the health check to tell a clinic what the agent
    will actually do, which is the single most common support question after a
    deployment.

    Args:
        env: Environment mapping; defaults to ``os.environ``.

    Returns:
        A JSON-serializable description, including whether a credential was
        found (never the credential itself).
    """
    config = LlmProviderConfig.from_env(env)
    ready = not config.is_disabled and bool(config.api_key)
    if config.kind == PROVIDER_ANTHROPIC and not config.api_key:
        ready = False
    return {
        "provider": config.kind,
        "model": config.model,
        "base_url": config.base_url,
        "credential_present": bool(config.api_key),
        "ready": ready,
        "note": _setup_note(config),
    }


def _setup_note(config: LlmProviderConfig) -> str:
    """Plain-language next step for the operator."""
    if config.is_disabled:
        return "No model configured: the agent decides with the rule engine only."
    if not config.api_key:
        return "No credential found: the agent falls back to the rule engine."
    return "Model configured: the agent asks it to decide, guarded by clinic policy."
