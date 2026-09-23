"""
Messaging tool layer for the patient follow-up agent.

This package exposes deterministic, LLM-callable tools that deliver messages
over WhatsApp / SMS / email. The design contract is:

* The **LLM decides** whether to send, through which channel, using which
  template, and what to do when a send fails.
* The **tools execute** exactly one attempt, then report a structured result.
* A tool **never raises**. Provider errors -- "recipient not verified",
  "template not found", "outside messaging window", rate limits, network
  failures -- are normalized into :class:`~tools.errors.SendErrorCode` values
  and returned to the model, which can then fall back to another channel or
  escalate to staff.

Quick start::

    from tools import build_tool_registry, MessagingConfig

    registry = build_tool_registry(MessagingConfig.from_env())
    result = registry.call("send_whatsapp_message", {
        "recipient": "+15550001111",
        "template": "appointment_reminder",
        "params": ["Alex", "dental cleaning", "+15550002222"],
    })
    print(result.success, result.error_code, result.hint)

Without an explicit ``MESSAGING_DRY_RUN=0`` every channel reports
``simulated=True`` so the whole agent loop, the demo and the tests run offline
and a populated ``.env`` can never message a real patient.
"""

from tools.aws_providers import AwsPinpointSmsProvider, AwsSesEmailProvider
from tools.config import DEFAULT_TEMPLATES, MessagingConfig, TemplateSpec
from tools.errors import SendErrorCode
from tools.providers import (
    MessageProvider,
    MetaWhatsAppProvider,
    ProviderOutcome,
    SendRequest,
    SmtpEmailProvider,
    TwilioProvider,
    build_default_providers,
)
from tools.result import ToolResult
from tools.transport import FakeTransport, HttpResponse, HttpTransport, UrllibTransport

__all__ = [
    "CHANNEL_ORDER",
    "DEFAULT_TEMPLATES",
    "AwsPinpointSmsProvider",
    "AwsSesEmailProvider",
    "FakeTransport",
    "HttpResponse",
    "HttpTransport",
    "MessageProvider",
    "MessagingConfig",
    "MetaWhatsAppProvider",
    "ProviderOutcome",
    "SendErrorCode",
    "SendRequest",
    "SmtpEmailProvider",
    "TOOL_NAMES",
    "TemplateSpec",
    "ToolResult",
    "TwilioProvider",
    "UrllibTransport",
    "build_default_providers",
    "build_tool_registry",
    "get_tool_schemas",
]

__version__ = "1.0.0"


def __getattr__(name: str):
    """
    Expose the tool-name constants without importing the heavy modules eagerly.

    ``tools.messaging`` imports the providers, so importing it from the package
    body would create an import cycle; PEP 562 module ``__getattr__`` keeps the
    convenience of ``tools.TOOL_NAMES`` without the cycle.

    Args:
        name: Attribute requested on the package.

    Returns:
        The requested constant.

    Raises:
        AttributeError: For any other name.
    """
    if name in {"TOOL_NAMES", "CHANNEL_ORDER"}:
        from tools.messaging import CHANNEL_ORDER, TOOL_NAMES

        return {"TOOL_NAMES": TOOL_NAMES, "CHANNEL_ORDER": CHANNEL_ORDER}[name]
    raise AttributeError(f"module 'tools' has no attribute {name!r}")


def build_tool_registry(*args, **kwargs):
    """
    Build the messaging tool registry (lazy import to avoid cycles).

    See :func:`tools.messaging.build_tool_registry` for the full signature.
    """
    from tools.messaging import build_tool_registry as _build

    return _build(*args, **kwargs)


def get_tool_schemas(*args, **kwargs):
    """
    Return the LLM-facing tool schemas (lazy import to avoid cycles).

    See :func:`tools.schemas.get_tool_schemas`.
    """
    from tools.schemas import get_tool_schemas as _get

    return _get(*args, **kwargs)
