"""Shared pytest fixtures: an in-process mock OpenAI-compatible backend.

Tests exercise the real request-building/forwarding code path with no
real sockets involved: `httpx.ASGITransport` lets `Transport` (and, above
it, `proxy.create_app`) talk directly to this mock backend's ASGI app in
memory. That means test failures point at real bugs in CodexProbe's
header/body handling rather than at network flakiness.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import AsyncIterator

import httpx
import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from codex_probe.config import BackendConfig


def make_mock_backend_app() -> FastAPI:
    """A tiny OpenAI-compatible backend used across the test suite.

    Every request it receives is appended to
    ``app.state.received_requests`` (method, path, headers, raw body) so
    tests can assert on what actually reached the "backend", not just
    what CodexProbe intended to send.
    """
    app = FastAPI()
    app.state.received_requests = []

    def _record(request: Request, body: bytes) -> None:
        app.state.received_requests.append(
            {
                "method": request.method,
                "path": request.url.path,
                "headers": dict(request.headers),
                "body": body,
            }
        )

    @app.post("/v1/echo")
    async def echo(request: Request):
        body = await request.body()
        _record(request, body)
        return JSONResponse(
            {"echo": json.loads(body) if body else None},
            headers={"x-backend": "mock"},
        )

    @app.get("/v1/models")
    async def models(request: Request):
        _record(request, b"")
        return JSONResponse({"data": [{"id": "mock-model"}]})

    @app.post("/v1/error")
    async def error(request: Request):
        body = await request.body()
        _record(request, body)
        return JSONResponse({"error": "bad request"}, status_code=400)

    @app.post("/v1/stream")
    async def stream(request: Request):
        body = await request.body()
        _record(request, body)

        async def event_source() -> AsyncIterator[bytes]:
            for i in range(3):
                yield f"data: {json.dumps({'chunk': i})}\n\n".encode()
            yield b"data: [DONE]\n\n"

        return StreamingResponse(event_source(), media_type="text/event-stream")

    return app


@pytest.fixture
def mock_backend_app() -> FastAPI:
    return make_mock_backend_app()


@pytest.fixture
def mock_transport(mock_backend_app: FastAPI) -> httpx.ASGITransport:
    return httpx.ASGITransport(app=mock_backend_app)


@pytest.fixture
def live_mock_backend():
    """A real mock backend bound to a real loopback TCP port.

    Unlike `mock_transport` (in-process ASGI, no sockets),
    `ProxyRecorder` binds a real socket via Uvicorn, so testing its full
    lifecycle needs a real backend on the other end too: this is what
    Codex would see if it were pointed at CodexProbe for real.
    """
    app = make_mock_backend_app()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning"))

    def run() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(server.serve())

    thread = threading.Thread(target=run, daemon=True, name="mock-backend-server")
    thread.start()

    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    if not server.started:
        raise RuntimeError("mock backend failed to start")

    port = server.servers[0].sockets[0].getsockname()[1]
    yield {"base_url": f"http://127.0.0.1:{port}", "app": app}

    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture
def backend_config() -> BackendConfig:
    return BackendConfig(
        name="mock-backend",
        base_url="http://mockbackend.test/v1",
        wire_api="chat_completions",
        api_key_env=None,
    )
