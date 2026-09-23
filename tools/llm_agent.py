"""
The LLM side of the contract: a tool-use loop over the Anthropic Messages API.

Responsibilities are split deliberately:

* The **model** decides whether to send, on which channel, with which template,
  and what to do after a failure.
* This loop only performs *plumbing*: call the model, dispatch ``tool_use``
  blocks through the registry, feed ``tool_result`` blocks back, repeat until
  the model stops asking for tools.

The loop is written against a tiny duck-typed client interface, so it can be
driven by the real ``anthropic`` SDK *or* by :class:`ScriptedModelClient` in
tests and demos -- no API key, network access or SDK required to prove the
failure-handling behaviour.

Only this module imports :mod:`anthropic`, and only lazily, so the rest of the
tool layer keeps working when the SDK is absent.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence
import json

from tools.messaging import ToolRegistry
from tools.result import ToolResult
from tools.schemas import get_tool_schemas

#: Default model used when the caller does not pick one.
DEFAULT_MODEL = "claude-sonnet-4-5"

#: Hard cap on model turns, so a tool-calling loop can never spin forever.
DEFAULT_MAX_ITERATIONS = 8

SYSTEM_PROMPT = (
    "You are the messaging component of a dental clinic's patient follow-up "
    "agent. You decide whether a patient needs a reminder and how to deliver "
    "it. You never compose a delivery attempt yourself: you call a tool, then "
    "act on the returned status.\n\n"
    "Rules:\n"
    "1. Prefer the patient's preferred channel.\n"
    "2. A tool call that returns status 'failed' is information, not a crash. "
    "Read error_code, retryable and suggested_fallback_channels.\n"
    "3. Never repeat a send on the same channel when error_code is "
    "non-retryable or when that channel just failed for this recipient. Move "
    "to a suggested fallback channel instead.\n"
    "4. If no channel can deliver the message, call escalate_to_staff with the "
    "error code as evidence, then summarise for the clinic.\n"
    "5. Business-initiated WhatsApp messages must use an approved template. "
    "Call list_message_templates if you are unsure of a name or parameter "
    "count.\n"
    "6. Treat 'simulated: true' results as dry-run: report them as simulated, "
    "never as delivered.\n"
    "7. Two AWS paths are available: send_sms and send_email. They are the "
    "clinic's production transports but can currently only reach verified test "
    "destinations, so a recipient_not_verified refusal means the channel is "
    "unavailable for this patient right now -- not that the patient refused. "
    "Pass a short 'reason' explaining your decision; it is logged for audit."
)


class MissingAnthropicError(RuntimeError):
    """Raised when the ``anthropic`` SDK is required but not installed."""


@dataclass
class ToolCall:
    """
    A ``tool_use`` block the model asked for.

    Attributes:
        id: Provider-assigned id, echoed back in the ``tool_result``.
        name: Tool name.
        arguments: Parsed argument object.
    """

    id: str
    name: str
    arguments: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ModelTurn:
    """
    A normalized assistant turn.

    Attributes:
        text: Concatenated text blocks.
        tool_calls: Requested tool calls, in order.
        stop_reason: Provider stop reason (``tool_use``, ``end_turn``, ...).
        raw_content: Untouched content blocks, appended back to the transcript.
    """

    text: str = ""
    tool_calls: List[ToolCall] = field(default_factory=list)
    stop_reason: str = "end_turn"
    raw_content: Any = None


@dataclass
class AgentRun:
    """
    Result of one complete agent turn.

    Attributes:
        text: The model's final natural-language answer.
        tool_results: Every :class:`ToolResult` produced during the run.
        transcript: Full message list sent to the model (for auditing/debug).
        iterations: Number of model calls made.
        stop_reason: Final stop reason.
        hit_iteration_cap: True when the turn limit ended the run.
    """

    text: str
    tool_results: List[ToolResult] = field(default_factory=list)
    transcript: List[Dict[str, Any]] = field(default_factory=list)
    iterations: int = 0
    stop_reason: str = "end_turn"
    hit_iteration_cap: bool = False

    @property
    def sent(self) -> List[ToolResult]:
        """
        Successful delivery attempts made during the run.

        Informational tools (``kind="info"``) report success too, so they are
        deliberately excluded: only a real send counts as "sent".
        """
        return [r for r in self.tool_results if r.success and r.kind == "send"]

    @property
    def failures(self) -> List[ToolResult]:
        """Failed attempts made during the run."""
        return [r for r in self.tool_results if not r.success]

    @property
    def escalated(self) -> bool:
        """Whether the agent handed the case to a human."""
        return any(
            r.success and r.kind == "handoff" for r in self.tool_results
        )

    @property
    def decision_support_calls(self) -> List[ToolResult]:
        """Informational tool calls (channel options, template catalogue)."""
        return [r for r in self.tool_results if r.success and r.kind == "info"]

    def delivered_result(self) -> Optional[ToolResult]:
        """
        The first successful, non-simulated delivery of the run.

        Returns:
            The :class:`ToolResult`, or ``None`` when nothing was delivered.
        """
        for result in self.sent:
            if not result.simulated:
                return result
        return None


def _get(block: Any, key: str, default: Any = None) -> Any:
    """Read ``key`` from a dict block or an SDK object block."""
    if isinstance(block, dict):
        return block.get(key, default)
    return getattr(block, key, default)


def _field(response: Any, key: str, default: Any = None) -> Any:
    """Read ``key`` from a dict response or an SDK response object."""
    if isinstance(response, dict):
        return response.get(key, default)
    return getattr(response, key, default)


def normalize_turn(content: Any, stop_reason: str = "end_turn") -> ModelTurn:
    """
    Convert an API response's content into a :class:`ModelTurn`.

    Accepts both plain dicts and ``anthropic`` SDK content objects.

    Args:
        content: The ``response.content`` list.
        stop_reason: The ``response.stop_reason`` value.

    Returns:
        The normalized turn.
    """
    text_parts: List[str] = []
    tool_calls: List[ToolCall] = []

    for block in content or []:
        block_type = _get(block, "type")
        if block_type == "text":
            text_parts.append(str(_get(block, "text", "") or ""))
        elif block_type == "tool_use":
            arguments = _get(block, "input", {}) or {}
            tool_calls.append(
                ToolCall(
                    id=str(_get(block, "id", "") or ""),
                    name=str(_get(block, "name", "") or ""),
                    arguments=dict(arguments) if isinstance(arguments, dict) else {},
                )
            )

    return ModelTurn(
        text="\n".join(part for part in text_parts if part).strip(),
        tool_calls=tool_calls,
        stop_reason=str(stop_reason or "end_turn"),
        raw_content=content,
    )


def create_anthropic_client(api_key: Optional[str] = None) -> Any:
    """
    Instantiate the real Anthropic client.

    Args:
        api_key: Optional explicit key; otherwise the SDK reads
            ``ANTHROPIC_API_KEY`` from the environment.

    Returns:
        An ``anthropic.Anthropic`` client.

    Raises:
        MissingAnthropicError: If the ``anthropic`` package is not installed.
    """
    try:
        import anthropic  # noqa: PLC0415 - intentionally lazy
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise MissingAnthropicError(
            "The 'anthropic' package is required for live LLM calls. "
            "Install it with 'pip install anthropic', or drive the loop with "
            "ScriptedModelClient for offline runs."
        ) from exc

    if api_key:
        return anthropic.Anthropic(api_key=api_key)
    return anthropic.Anthropic()


class ToolUseAgent:
    """
    Drives a Claude tool-use conversation against a :class:`ToolRegistry`.

    Args:
        registry: Registry of deterministic messaging tools.
        client: Anthropic (or compatible) client. Required for :meth:`run`;
            defaults to a real client when the SDK is available.
        model: Model name.
        system: System prompt; defaults to :data:`SYSTEM_PROMPT`.
        max_tokens: Response token budget.
        max_iterations: Maximum model turns before the loop stops.
        tool_names: Optional subset of tools to expose.
    """

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        client: Optional[Any] = None,
        model: str = DEFAULT_MODEL,
        system: str = SYSTEM_PROMPT,
        max_tokens: int = 1024,
        max_iterations: int = DEFAULT_MAX_ITERATIONS,
        tool_names: Optional[Sequence[str]] = None,
    ) -> None:
        self.registry = registry
        self.client = client
        self.model = model
        self.system = system
        self.max_tokens = max_tokens
        self.max_iterations = max_iterations
        self.tools = get_tool_schemas(tool_names)

    def _client(self) -> Any:
        """
        Return the client, creating one on first use if needed.

        Uses whichever vendor the clinic configured via ``AGENT_LLM_PROVIDER``,
        so the same choice governs the decision engine and this tool-use loop.
        """
        if self.client is None:
            from tools.llm_providers import create_llm_client

            client = create_llm_client()
            if client is None:
                raise MissingAnthropicError(
                    "No LLM is configured (AGENT_LLM_PROVIDER=disabled). Set a "
                    "provider in hospital_setup.py or pass a client explicitly."
                )
            self.client = client
        return self.client

    def run(
        self, user_message: str, *, messages: Optional[List[Dict[str, Any]]] = None
    ) -> AgentRun:
        """
        Run one full tool-use turn.

        Args:
            user_message: The instruction/task, typically a case briefing.
            messages: Optional pre-existing transcript to continue from.

        Returns:
            An :class:`AgentRun` describing what the model decided and every
            attempt the tools made.
        """
        client = self._client()
        transcript: List[Dict[str, Any]] = list(messages or [])
        transcript.append({"role": "user", "content": user_message})

        tool_results: List[ToolResult] = []
        iterations = 0
        text = ""
        stop_reason = "end_turn"
        hit_cap = False

        while iterations < self.max_iterations:
            iterations += 1
            response = client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=self.system,
                messages=transcript,
                tools=self.tools,
            )
            turn = normalize_turn(
                _field(response, "content"),
                _field(response, "stop_reason", "end_turn"),
            )
            stop_reason = turn.stop_reason

            transcript.append(
                {
                    "role": "assistant",
                    "content": turn.raw_content
                    if turn.raw_content is not None
                    else turn.text,
                }
            )

            if turn.text:
                text = turn.text

            if not turn.tool_calls:
                break

            result_blocks: List[Dict[str, Any]] = []
            for call in turn.tool_calls:
                result = self.registry.call(call.name, call.arguments)
                tool_results.append(result)
                result_blocks.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": call.id,
                        "content": result.to_json(),
                        "is_error": result.is_error,
                    }
                )
            transcript.append({"role": "user", "content": result_blocks})

            if turn.stop_reason != "tool_use":
                break
        else:
            hit_cap = True

        if hit_cap and not text:
            text = (
                f"Stopped after reaching the {self.max_iterations}-turn tool use "
                "limit without a final answer. Review the recorded tool results."
            )

        return AgentRun(
            text=text,
            tool_results=tool_results,
            transcript=transcript,
            iterations=iterations,
            stop_reason=stop_reason,
            hit_iteration_cap=hit_cap,
        )


class ScriptedModelClient:
    """
    A fake Anthropic client that replays (or computes) scripted responses.

    This is what makes the failure-handling logic testable and demonstrable
    without an API key. Responses can be:

    * a mapping: ``{"stop_reason": ..., "content": [...]}``,
    * an object with ``content``/``stop_reason`` attributes,
    * or a callable receiving the current ``messages`` list and returning one
      of the above. Callables are treated as **policies**: they stay in the
      script and are re-evaluated on every turn, so they can branch on the real
      tool results. Mappings/objects are consumed once.

    Every request is recorded in :attr:`requests` for assertions.
    """

    class _Messages:
        """Mimics ``client.messages``."""

        def __init__(self, owner: "ScriptedModelClient") -> None:
            self._owner = owner

        def create(self, **kwargs: Any) -> Any:
            """Return the next scripted response."""
            return self._owner._next_response(kwargs)

    def __init__(self, script: Sequence[Any]) -> None:
        """
        Args:
            script: Ordered responses (see class docstring).
        """
        self.script: List[Any] = list(script)
        self.requests: List[Dict[str, Any]] = []
        self.messages = ScriptedModelClient._Messages(self)

    def _next_response(self, kwargs: Dict[str, Any]) -> Any:
        """Record the request and produce the next response."""
        self.requests.append(dict(kwargs))
        if not self.script:
            return {"stop_reason": "end_turn", "content": []}
        step = self.script.pop(0)
        if callable(step):
            # Callables are policies, not one-shot steps: they are re-evaluated
            # on every turn so they can react to the tool results they receive.
            response = step(kwargs.get("messages") or [])
            self.script.insert(0, step)
            return response
        return step


def last_tool_result_payload(messages: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Extract the most recent ``tool_result`` payload from a transcript.

    Handy inside a scripted model step to branch on what actually happened.

    Args:
        messages: The message list handed to the model.

    Returns:
        The parsed payload dict, or ``None`` when there is no tool result yet.
    """
    for message in reversed(list(messages or [])):
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            continue
        for block in reversed(content):
            if isinstance(block, dict) and block.get("type") == "tool_result":
                raw = block.get("content")
                if isinstance(raw, str):
                    try:
                        return json.loads(raw)
                    except ValueError:
                        return {"raw": raw}
                if isinstance(raw, dict):
                    return raw
    return None
