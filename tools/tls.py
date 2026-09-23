"""
TLS trust configuration for outbound connections.

Python's ``ssl.create_default_context()`` reads the *system* trust store. On a
macOS python.org install that store is often empty until ``Install
Certificates.command`` is run, and on slim containers it may be missing
entirely, so every TLS handshake fails with
``CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate`` -- which
looks like a provider outage but is really a local trust problem.

This module prefers a bundle that actually exists, in this order:

1. ``SSL_CERT_FILE`` -- an explicit operator choice always wins.
2. ``certifi``        -- the CA bundle shipped with the Python ecosystem.
3. the system default -- unchanged behaviour when nothing else is available.

Providers call :func:`default_ssl_context` instead of
``ssl.create_default_context`` so that real sends work on a stock developer
machine without the caller having to export anything.
"""

from __future__ import annotations
import os
import ssl
from typing import Dict, Optional


def ca_bundle_path() -> Optional[str]:
    """
    Return a CA bundle path that exists, or ``None`` to use the system store.

    A path is only returned when the file is really present: pointing OpenSSL at
    a missing file raises on context creation, which would turn "no bundle
    configured" into a crash.
    """
    explicit = os.environ.get("SSL_CERT_FILE")
    if explicit and os.path.exists(explicit):
        return explicit

    try:
        import certifi
    except ImportError:
        return None

    try:
        path = certifi.where()
    except Exception:
        return None
    return path if path and os.path.exists(path) else None


_CONTEXTS: Dict[str, ssl.SSLContext] = {}


def default_ssl_context() -> ssl.SSLContext:
    """
    Build (and cache) a client TLS context that can actually verify peers.

    Cached per bundle because contexts are expensive to construct and are only
    ever used for client connections here. The cache key keeps the
    ``SSL_CERT_FILE`` and system-default cases separate.
    """
    bundle = ca_bundle_path()
    key = bundle or ""
    context = _CONTEXTS.get(key)
    if context is None:
        if bundle:
            context = ssl.create_default_context(cafile=bundle)
        else:
            context = ssl.create_default_context()
        _CONTEXTS[key] = context
    return context


def reset_cache() -> None:
    """Drop the cached contexts. For tests that change ``SSL_CERT_FILE``."""
    _CONTEXTS.clear()
