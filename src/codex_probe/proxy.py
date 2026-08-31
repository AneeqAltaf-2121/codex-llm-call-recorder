"""OpenAI-compatible reverse proxy: the local HTTP endpoint Codex talks to.

Hosts a catch-all ASGI app that accepts any method/path Codex sends
(``POST /v1/responses``, ``POST /v1/chat/completions``, ``GET /v1/models``,
...), forwards it to the configured backend via :mod:`codex_probe.transport`,
and -- if given an ``on_call`` handler -- reports a complete
:class:`~codex_probe.models.CapturedCall` for every request once it
finishes. Codex's observed behavior (status code, headers, body) is
preserved exactly; the proxy adds nothing and removes nothing except the
hop-by-hop headers that must never be forwarded (see
:mod:`codex_probe.headers`).

Non-streaming responses are fully buffered and returned in one piece.
Streaming (SSE) responses are detected via ``Content-Type`` and handed to
:mod:`codex_probe.streaming`, which forwards chunks to Codex the instant
they arrive while simultaneously capturing them for the log -- Codex sees
identical streaming behavior whether CodexProbe is in the path or not.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable
from itertools import count

from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse

from codex_probe.config import RecorderConfig
from codex_probe.headers import (
    apply_backend_auth,
    filter_forward_headers,
    filter_response_headers,
    redact_headers_for_log,
)
from codex_probe.models import (
    BackendInfo,
    CapturedCall,
    RequestRecord,
    ResponseRecord,
    decode_body,
    utc_timestamp,
)
from codex_probe.streaming import looks_like_event_stream, stream_and_capture
from codex_probe.transport import Transport

#: Called once per finished call, with the complete captured record.
CallHandler = Callable[[CapturedCall], Awaitable[None]]

_PROXIED_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]


def create_app(
    *,
    config: RecorderConfig,
    transport: Transport,
    session_id: str,
    on_call: CallHandler | None = None,
) -> FastAPI:
    """Build the CodexProbe FastAPI app for one recorder session.

    A fresh sequence counter is created per app/session, so
    ``CapturedCall.sequence`` always starts at 1 for a new session --
    matching the per-session log files written by
    :mod:`codex_probe.logging_store`.
    """
    app = FastAPI(title="CodexProbe", docs_url=None, redoc_url=None, openapi_url=None)
    sequence_counter = count(start=1)

    @app.api_route("/{full_path:path}", methods=_PROXIED_METHODS)
    async def catch_all(full_path: str, request: Request) -> Response:
        method = request.method
        path = "/" + full_path
        request_body = await request.body()
        incoming_headers = dict(request.headers)

        forward_headers = filter_forward_headers(incoming_headers)
        forward_headers = apply_backend_auth(forward_headers, config.backend)

        upstream_request = transport.build_request(
            method=method,
            path=path,
            headers=forward_headers,
            params=list(request.query_params.multi_items()),
            content=request_body,
        )

        started_at = time.perf_counter()
        upstream_response = await transport.send(upstream_request)
        sequence = next(sequence_counter)
        streaming = looks_like_event_stream(upstream_response.headers.get("content-type", ""))
        response_headers = filter_response_headers(dict(upstream_response.headers))

        if streaming:

            async def on_complete(
                body: bytes, status_code: int, raw_headers: dict[str, str]
            ) -> None:
                await _record_call(
                    on_call=on_call,
                    config=config,
                    session_id=session_id,
                    sequence=sequence,
                    method=method,
                    path=path,
                    incoming_headers=incoming_headers,
                    request_body=request_body,
                    status_code=status_code,
                    response_headers_raw=raw_headers,
                    response_body=body,
                    streaming=True,
                    latency_ms=(time.perf_counter() - started_at) * 1000,
                )

            return StreamingResponse(
                stream_and_capture(upstream_response, on_complete=on_complete),
                status_code=upstream_response.status_code,
                headers=response_headers,
            )

        response_body = await upstream_response.aread()
        await upstream_response.aclose()
        latency_ms = (time.perf_counter() - started_at) * 1000

        await _record_call(
            on_call=on_call,
            config=config,
            session_id=session_id,
            sequence=sequence,
            method=method,
            path=path,
            incoming_headers=incoming_headers,
            request_body=request_body,
            status_code=upstream_response.status_code,
            response_headers_raw=dict(upstream_response.headers),
            response_body=response_body,
            streaming=False,
            latency_ms=latency_ms,
        )

        return Response(
            content=response_body,
            status_code=upstream_response.status_code,
            headers=response_headers,
        )

    return app


async def _record_call(
    *,
    on_call: CallHandler | None,
    config: RecorderConfig,
    session_id: str,
    sequence: int,
    method: str,
    path: str,
    incoming_headers: dict[str, str],
    request_body: bytes,
    status_code: int,
    response_headers_raw: dict[str, str],
    response_body: bytes,
    streaming: bool,
    latency_ms: float,
) -> None:
    """Assemble a `CapturedCall` and hand it to `on_call`, if one is set."""
    if on_call is None:
        return
    call = CapturedCall(
        call_id=str(uuid.uuid4()),
        sequence=sequence,
        session_id=session_id,
        timestamp=utc_timestamp(),
        backend=BackendInfo(
            name=config.backend.name,
            base_url=config.backend.base_url,
            wire_api=config.backend.wire_api,
        ),
        request=RequestRecord(
            method=method,
            path=path,
            headers=redact_headers_for_log(incoming_headers),
            body=decode_body(request_body),
        ),
        response=ResponseRecord(
            status_code=status_code,
            headers=redact_headers_for_log(response_headers_raw),
            body=decode_body(response_body),
        ),
        streaming=streaming,
        latency_ms=latency_ms,
    )
    await on_call(call)
