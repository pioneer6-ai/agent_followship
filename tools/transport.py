"""
Transport and connection doubles for provider calls.

Providers never touch ``requests``/``urllib``/``smtplib`` directly: they are
handed a transport (HTTP) or a connection factory (SMTP). That keeps the
error-classification logic -- the interesting part -- fully unit-testable
offline, because tests and demos can inject :class:`FakeTransport` and
:class:`FakeSmtpConnection` and replay realistic provider failures.

A transport never raises. Connection-level problems are reported as a
response with ``status_code == 0`` plus an ``error`` string.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Any, Dict, List, Optional, Protocol
import json
import smtplib
import time
import urllib.error
import urllib.parse
import urllib.request


@dataclass
class HttpResponse:
    """
    Provider response, normalized.

    Attributes:
        status_code: HTTP status, or ``0`` when the connection itself failed.
        body: Parsed JSON when possible, otherwise the raw text.
        headers: Response headers (lowercased keys not guaranteed).
        latency_ms: Wall-clock duration of the call.
        error: Transport-level error description; only set when status is ``0``.
    """

    status_code: int
    body: Any = None
    headers: Dict[str, str] = field(default_factory=dict)
    latency_ms: int = 0
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        """True for a 2xx response."""
        return 200 <= self.status_code < 300

    def json_body(self) -> Dict[str, Any]:
        """Return the body as a dict, or an empty dict if it is not one."""
        return self.body if isinstance(self.body, dict) else {}

    def text(self) -> str:
        """Return the body as text (used when it is not JSON)."""
        if self.body is None:
            return ""
        if isinstance(self.body, str):
            return self.body
        return json.dumps(self.body, ensure_ascii=False)


class HttpTransport(Protocol):
    """Minimal transport surface required by the providers."""

    def post_json(
        self,
        url: str,
        *,
        headers: Optional[Dict[str, str]] = None,
        payload: Optional[Dict[str, Any]] = None,
        timeout: float = 10.0,
    ) -> HttpResponse:
        """POST a JSON body and return a normalized response."""

    def post_form(
        self,
        url: str,
        *,
        headers: Optional[Dict[str, str]] = None,
        data: Optional[Dict[str, str]] = None,
        timeout: float = 10.0,
    ) -> HttpResponse:
        """POST an ``application/x-www-form-urlencoded`` body."""


def _decode(raw: bytes) -> Any:
    """Decode a response body as JSON, falling back to text."""
    if not raw:
        return None
    try:
        text = raw.decode("utf-8", errors="replace")
    except Exception:  # pragma: no cover - defensive
        return None
    try:
        return json.loads(text)
    except ValueError:
        return text


class UrllibTransport:
    """
    Real transport built on the standard library (no extra dependencies).

    ``urllib`` raises on non-2xx responses, so this class catches
    ``HTTPError`` and converts it into a normal :class:`HttpResponse` --
    provider error bodies are exactly what we need for error classification.
    """

    def post_json(
        self,
        url: str,
        *,
        headers: Optional[Dict[str, str]] = None,
        payload: Optional[Dict[str, Any]] = None,
        timeout: float = 10.0,
    ) -> HttpResponse:
        """POST ``payload`` as JSON."""
        merged = {"Content-Type": "application/json"}
        merged.update(headers or {})
        body = json.dumps(payload or {}).encode("utf-8")
        return self._post(url, merged, body, timeout)

    def post_form(
        self,
        url: str,
        *,
        headers: Optional[Dict[str, str]] = None,
        data: Optional[Dict[str, str]] = None,
        timeout: float = 10.0,
    ) -> HttpResponse:
        """POST ``data`` as a urlencoded form."""
        merged = {"Content-Type": "application/x-www-form-urlencoded"}
        merged.update(headers or {})
        body = urllib.parse.urlencode(data or {}).encode("utf-8")
        return self._post(url, merged, body, timeout)

    def _post(
        self,
        url: str,
        headers: Dict[str, str],
        body: bytes,
        timeout: float,
    ) -> HttpResponse:
        """Perform the POST, converting every failure into a response object."""
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        start = time.perf_counter()

        def elapsed() -> int:
            return int((time.perf_counter() - start) * 1000)

        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return HttpResponse(
                    status_code=response.status,
                    body=_decode(response.read()),
                    headers=dict(response.headers or {}),
                    latency_ms=elapsed(),
                )
        except urllib.error.HTTPError as exc:
            # Provider said "no" -- the body carries the error code we need.
            return HttpResponse(
                status_code=exc.code,
                body=_decode(exc.read()),
                headers=dict(exc.headers or {}),
                latency_ms=elapsed(),
            )
        except Exception as exc:  # URLError, socket.timeout, ssl errors, ...
            return HttpResponse(
                status_code=0,
                body=None,
                latency_ms=elapsed(),
                error=f"{type(exc).__name__}: {exc}",
            )


class FakeTransport:
    """
    Scripted transport for tests and offline demos.

    Responses are returned in order; once the script is exhausted the
    ``default`` response is used repeatedly. Every call is recorded in
    :attr:`requests` so tests can assert on the exact payload sent.
    """

    def __init__(
        self,
        responses: Optional[List[Any]] = None,
        default: Optional[HttpResponse] = None,
    ) -> None:
        """
        Args:
            responses: Scripted responses. Each may be an
                :class:`HttpResponse`, a plain mapping such as
                ``{"status_code": 400, "body": {...}}``, or a callable
                returning either.
            default: Response used once the script is exhausted.
        """
        self._responses: List[Any] = [self._coerce(item) for item in responses or []]
        self.default = self._coerce(default) if default is not None else HttpResponse(
            status_code=200,
            body={"messages": [{"id": "wamid.SIMULATED"}]},
        )
        self.requests: List[Dict[str, Any]] = []

    @staticmethod
    def _coerce(item: Any) -> Any:
        """
        Normalize a scripted response.

        Accepts an :class:`HttpResponse`, a mapping describing one, or a
        callable that returns either (resolved at call time).

        Args:
            item: The scripted response.

        Returns:
            An :class:`HttpResponse`, a callable, or the input unchanged.
        """
        if isinstance(item, HttpResponse) or callable(item):
            return item
        if isinstance(item, dict):
            return HttpResponse(
                status_code=int(item.get("status_code", 200)),
                body=item.get("body"),
                headers=dict(item.get("headers") or {}),
                latency_ms=int(item.get("latency_ms", 0)),
                error=item.get("error"),
            )
        return item

    def push(self, response: Any) -> None:
        """Append an extra scripted response."""
        self._responses.append(self._coerce(response))

    def queue_error(self, message: str = "connection failed") -> None:
        """
        Queue a simulated connection-level failure (``status_code == 0``).

        Args:
            message: Error text recorded on the response.
        """
        self.push(HttpResponse(status_code=0, error=message))

    def post_json(
        self,
        url: str,
        *,
        headers: Optional[Dict[str, str]] = None,
        payload: Optional[Dict[str, Any]] = None,
        timeout: float = 10.0,
    ) -> HttpResponse:
        """Record the call and return the next scripted response."""
        self.requests.append(
            {"kind": "json", "url": url, "headers": headers or {}, "payload": payload}
        )
        return self._next()

    def post_form(
        self,
        url: str,
        *,
        headers: Optional[Dict[str, str]] = None,
        data: Optional[Dict[str, str]] = None,
        timeout: float = 10.0,
    ) -> HttpResponse:
        """Record the call and return the next scripted response."""
        self.requests.append(
            {"kind": "form", "url": url, "headers": headers or {}, "data": data}
        )
        return self._next()

    def _next(self) -> HttpResponse:
        """Pop the next scripted response, or fall back to the default."""
        if not self._responses:
            return self.default
        nxt = self._responses.pop(0)
        if callable(nxt):
            return self._coerce(nxt())
        return nxt


class FakeSmtpConnection:
    """
    Scripted ``smtplib`` stand-in for offline tests and demos.

    Mirrors the subset of the ``SMTP``/``SMTP_SSL`` surface that
    :class:`~tools.providers.SmtpEmailProvider` uses, and can be told to fail
    at any stage with a realistic ``smtplib`` exception.

    Attributes:
        sent: Messages that reached :meth:`send_message`.
        transcript: Every method call, for assertions.
    """

    def __init__(
        self,
        *,
        recipients_refused: Optional[Dict[str, Any]] = None,
        auth_failure: bool = False,
        connect_failure: bool = False,
        disconnect_on_quit: bool = False,
    ) -> None:
        """
        Args:
            recipients_refused: Mapping of address to ``(code, message)`` that
                :meth:`send_message` should reject.
            auth_failure: Make :meth:`login` raise ``SMTPAuthenticationError``.
            connect_failure: Make :meth:`connect` raise ``SMTPConnectError``.
            disconnect_on_quit: Make :meth:`quit` raise, to prove a broken
                hand-off cannot mask a successful send.
        """
        self.recipients_refused = recipients_refused or {}
        self.auth_failure = auth_failure
        self.connect_failure = connect_failure
        self.disconnect_on_quit = disconnect_on_quit
        self.sent: List[Any] = []
        self.transcript: List[str] = []
        self.closed = False
        self.tls_started = False

    def connect(self, host: str, port: int) -> Any:
        """Record and (maybe) fail the connection attempt."""
        self.transcript.append(f"connect:{host}:{port}")
        if self.connect_failure:
            raise smtplib.SMTPConnectError(421, f"Cannot connect to {host}:{port}")
        return (220, b"ready")

    def ehlo(self) -> Any:
        """Record the EHLO handshake."""
        self.transcript.append("ehlo")
        return (250, b"ok")

    def starttls(self, **kwargs: Any) -> Any:
        """Record the STARTTLS upgrade."""
        self.transcript.append("starttls")
        self.tls_started = True
        return (220, b"go ahead")

    def login(self, username: str, password: str) -> Any:
        """Record and (maybe) fail authentication."""
        self.transcript.append(f"login:{username}")
        if self.auth_failure:
            raise smtplib.SMTPAuthenticationError(
                535, b"5.7.8 Username and Password not accepted"
            )
        return (235, b"accepted")

    def send_message(self, message: EmailMessage) -> Any:
        """Record the message, or raise a refusal for scripted recipients."""
        destination = str(message.get("To", ""))
        self.transcript.append(f"send:{destination}")
        if destination in self.recipients_refused:
            raise smtplib.SMTPRecipientsRefused(
                {destination: self.recipients_refused[destination]}
            )
        self.sent.append(message)
        return {}

    def quit(self) -> Any:
        """Record the QUIT; optionally fail to prove it cannot mask a send."""
        self.transcript.append("quit")
        if self.disconnect_on_quit:
            raise smtplib.SMTPServerDisconnected("connection reset by peer")
        return (221, b"bye")

    def close(self) -> None:
        """Record the socket close."""
        self.closed = True
        self.transcript.append("close")
