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

    def __init__(
        self,
        backend: BackendConfig,
        timeout: float = 600.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """Create a transport for ``backend``.

        ``transport`` is exposed purely for testability: passing an
        ``httpx.ASGITransport`` wired to an in-process mock backend lets
        tests exercise the real request-building/forwarding code path
        with no real sockets involved. Production code leaves it unset,
        which makes httpx use a real network connection.
        """
        self._backend = backend
        self._client = httpx.AsyncClient(
            base_url=backend.base_url, timeout=timeout, transport=transport
        )

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
        """Build (but do not send) a request against the backend base URL.

        ``path`` is the full path Codex sent to the proxy, typically
        including an API-version prefix such as ``/v1`` (Codex's own
        ``model_provider.base_url`` is configured *with* that prefix, e.g.
        ``http://127.0.0.1:8135/v1``, and it appends bare endpoint names
        like ``responses`` to it). ``httpx.AsyncClient`` concatenates its
        ``base_url``'s own path with whatever path is passed here rather
        than replacing it (unlike a plain RFC 3986 URL join), so if
        ``path`` still carried that same prefix and the backend's
        ``base_url`` also ends in ``/v1`` (as both OpenAI and typical
        OpenAI-compatible servers do), forwarding it unmodified would
        produce ``.../v1/v1/responses`` instead of ``.../v1/responses``.
        Stripping a leading prefix shared with the backend's own
        ``base_url`` path keeps the two from stacking.
        """
        relative_path = _strip_shared_prefix(path, self._client.base_url.path)
        return self._client.build_request(
            method,
            relative_path,
            headers=headers,
            params=params,  # type: ignore[arg-type]  # list[tuple[str, str]] is accepted at runtime
            content=content,
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


def _strip_shared_prefix(path: str, base_path: str) -> str:
    """Remove a leading ``base_path`` segment from ``path``, if present."""
    base_path = base_path.rstrip("/")
    if not base_path:
        return path
    if path == base_path:
        return ""
    if path.startswith(base_path + "/"):
        return path[len(base_path) :]
    return path
