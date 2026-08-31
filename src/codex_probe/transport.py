"""Upstream HTTP transport: talks to the configured real LLM backend.

This module knows the backend's base URL, and how to send one HTTP
request to it and get a response back. It knows nothing about Codex,
proxy routing, sessions, or log files -- that separation is what lets
``proxy.py`` and ``streaming.py`` be tested against a fake transport, and
lets this module be tested against a real backend without dragging in the
rest of the recorder.

A single :class:`httpx.AsyncClient` is created per :class:`Transport` and
reused for every request in a session, giving connection pooling and
keep-alive behavior representative of a real client -- rather than paying
a fresh TCP/TLS handshake for every call, which is both slower and less
faithful to how Codex would behave talking to the backend directly.
"""

from __future__ import annotations

import httpx

from codex_probe.config import BackendConfig
from codex_probe.errors import UpstreamError


class Transport:
    """Persistent async HTTP client bound to a single configured backend."""

    def __init__(self, backend: BackendConfig, timeout: float = 600.0) -> None:
        self._backend = backend
        self._client = httpx.AsyncClient(base_url=backend.base_url, timeout=timeout)

    @property
    def backend(self) -> BackendConfig:
        return self._backend

    def build_request(
        self,
        *,
        method: str,
        path: str,
        headers: dict[str, str],
        params: list[tuple[str, str]] | None,
        content: bytes,
    ) -> httpx.Request:
        """Build (but do not send) a request against the backend base URL."""
        return self._client.build_request(
            method, path, headers=headers, params=params, content=content
        )

    async def send(self, request: httpx.Request) -> httpx.Response:
        """Send ``request`` upstream and return a response with an open stream.

        The response body is deliberately *not* read here -- ``stream=True``
        leaves the connection open so a caller can forward bytes to Codex as
        they arrive (see ``streaming.py``) instead of buffering the entire
        backend response before Codex sees any of it. Callers that don't
        need streaming can simply ``await response.aread()`` and then
        ``await response.aclose()``.
        """
        try:
            return await self._client.send(request, stream=True)
        except httpx.HTTPError as exc:
            raise UpstreamError(
                f"failed to reach backend '{self._backend.name}' at {self._backend.base_url}: {exc}"
            ) from exc

    async def aclose(self) -> None:
        """Close the underlying connection pool. Call once per session."""
        await self._client.aclose()
