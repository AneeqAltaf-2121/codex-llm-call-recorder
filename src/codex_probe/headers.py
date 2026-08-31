"""Safe HTTP header handling: passthrough, auth injection, and redaction.

Three distinct concerns live here, each with its own function so the
proxy layer can compose them explicitly instead of one function silently
doing all three:

1. Which headers are safe to forward from Codex to the upstream backend
   (``filter_forward_headers``).
2. How backend authentication gets injected from configuration rather
   than relying on whatever Codex happened to send (``apply_backend_auth``).
3. How headers are redacted before they are written to a log file, so
   captured sessions can be shared without leaking API keys
   (``redact_headers_for_log``).
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from codex_probe.config import BackendConfig

# Headers that describe a single network hop rather than the LLM request
# itself (RFC 7230 sec. 6.1, plus common proxy variants). These must never
# be blindly forwarded: CodexProbe makes its own, separate connection to
# the backend with its own connection/transfer-encoding behavior, so
# replaying Codex's hop-by-hop headers verbatim would corrupt that
# connection rather than describe it faithfully.
HOP_BY_HOP_HEADERS = frozenset(
    {
        "connection",
        "keep-alive",
        "transfer-encoding",
        "upgrade",
        "proxy-authenticate",
        "proxy-authorization",
        "proxy-connection",
        "te",
        "trailer",
    }
)

# Headers scoped to the (Codex -> CodexProbe) hop that the outgoing HTTP
# client must regenerate for the (CodexProbe -> backend) hop rather than
# have forwarded verbatim (a stale Host header would misroute the
# backend request; a stale Content-Length would desync the connection if
# httpx re-encodes anything).
REQUEST_SCOPED_HEADERS = frozenset({"host", "content-length"})

# Header names whose *values* must never be written to disk in plaintext.
SENSITIVE_HEADER_NAMES = frozenset(
    {"authorization", "api-key", "x-api-key", "openai-api-key", "proxy-authorization"}
)

_REDACTED = "[REDACTED]"


def filter_forward_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Return the subset of ``headers`` safe to forward to the backend."""
    dropped = HOP_BY_HOP_HEADERS | REQUEST_SCOPED_HEADERS
    return {name: value for name, value in headers.items() if name.lower() not in dropped}


def apply_backend_auth(headers: Mapping[str, str], backend: BackendConfig) -> dict[str, str]:
    """Inject backend authentication from configuration, if configured.

    When ``backend.api_key_env`` names an environment variable that is
    set, its value overrides any ``Authorization`` header Codex sent --
    this is what lets CodexProbe authenticate to the real backend
    independently of however Codex itself was configured. Otherwise
    ``headers`` is returned unchanged (local backends such as Ollama
    typically require no authentication at all).
    """
    result = dict(headers)
    if backend.api_key_env:
        api_key = os.environ.get(backend.api_key_env)
        if api_key:
            result["authorization"] = f"Bearer {api_key}"
    return result


def redact_headers_for_log(headers: Mapping[str, str]) -> dict[str, str]:
    """Return a copy of ``headers`` with sensitive values redacted.

    Used only on the path into the log store -- the proxy still sees and
    forwards the real values; only the on-disk record is redacted, so a
    captured session's logs can be inspected or shared without leaking
    credentials.
    """
    redacted: dict[str, str] = {}
    for name, value in headers.items():
        if name.lower() in SENSITIVE_HEADER_NAMES:
            if isinstance(value, str) and value.lower().startswith("bearer "):
                redacted[name] = f"Bearer {_REDACTED}"
            else:
                redacted[name] = _REDACTED
        else:
            redacted[name] = value
    return redacted
