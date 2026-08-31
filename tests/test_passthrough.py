"""Non-streaming reverse proxy passthrough tests.

Architecture under test:

    TestClient -> CodexProbe proxy app -> Transport -> mock backend app

Both hops are in-process ASGI (via `httpx.ASGITransport`), so these tests
prove CodexProbe's own header/body handling is faithful without depending
on real sockets or an external service.
"""

from __future__ import annotations

import json

import pytest
from starlette.testclient import TestClient

from codex_probe.config import RecorderConfig
from codex_probe.proxy import create_app
from codex_probe.transport import Transport


@pytest.fixture
def proxy_client(backend_config, mock_transport, mock_backend_app, tmp_path):
    config = RecorderConfig(backend=backend_config, log_dir=tmp_path / "logs")
    transport = Transport(backend_config, transport=mock_transport)
    captured_calls = []

    async def on_call(call):
        captured_calls.append(call)

    app = create_app(config=config, transport=transport, session_id="test-session", on_call=on_call)
    client = TestClient(app)
    client.captured_calls = captured_calls
    client.mock_backend_app = mock_backend_app
    return client


def test_request_body_reaches_backend_unchanged(proxy_client):
    payload = {"hello": "world", "nested": {"a": [1, 2, 3]}}

    response = proxy_client.post("/v1/echo", json=payload)

    assert response.status_code == 200
    received = proxy_client.mock_backend_app.state.received_requests
    assert len(received) == 1
    assert json.loads(received[0]["body"]) == payload
    assert received[0]["method"] == "POST"
    assert received[0]["path"] == "/v1/echo"


def test_response_returned_unchanged_to_client(proxy_client):
    response = proxy_client.post("/v1/echo", json={"a": 1})

    assert response.json() == {"echo": {"a": 1}}
    assert response.headers["x-backend"] == "mock"


def test_get_request_passthrough(proxy_client):
    response = proxy_client.get("/v1/models")

    assert response.status_code == 200
    assert response.json() == {"data": [{"id": "mock-model"}]}


def test_backend_error_status_is_forwarded_not_swallowed(proxy_client):
    response = proxy_client.post("/v1/error", json={"bad": True})

    assert response.status_code == 400
    assert response.json() == {"error": "bad request"}


def test_authorization_header_is_forwarded(proxy_client):
    proxy_client.post("/v1/echo", json={}, headers={"authorization": "Bearer test-token"})

    received = proxy_client.mock_backend_app.state.received_requests
    assert received[-1]["headers"]["authorization"] == "Bearer test-token"


def test_backend_api_key_env_overrides_forwarded_authorization(monkeypatch, backend_config, mock_transport, mock_backend_app, tmp_path):
    monkeypatch.setenv("MOCK_API_KEY", "sk-from-config")
    backend = backend_config.model_copy(update={"api_key_env": "MOCK_API_KEY"})
    config = RecorderConfig(backend=backend, log_dir=tmp_path / "logs")
    transport = Transport(backend, transport=mock_transport)
    app = create_app(config=config, transport=transport, session_id="s")
    client = TestClient(app)

    client.post("/v1/echo", json={}, headers={"authorization": "Bearer whatever-codex-sent"})

    received = mock_backend_app.state.received_requests
    assert received[-1]["headers"]["authorization"] == "Bearer sk-from-config"


def test_call_is_captured_with_complete_request_and_response_body(proxy_client):
    payload = {"model": "gpt-5", "input": "hi", "tools": [{"name": "shell"}]}

    proxy_client.post("/v1/echo", json=payload)

    assert len(proxy_client.captured_calls) == 1
    call = proxy_client.captured_calls[0]
    assert call.request.body == payload
    assert call.response.body == {"echo": payload}
    assert call.streaming is False
    assert call.sequence == 1
    assert call.session_id == "test-session"
    assert call.backend.name == "mock-backend"


def test_multiple_calls_are_sequenced_in_order(proxy_client):
    proxy_client.post("/v1/echo", json={"n": 1})
    proxy_client.post("/v1/echo", json={"n": 2})
    proxy_client.post("/v1/echo", json={"n": 3})

    sequences = [c.sequence for c in proxy_client.captured_calls]
    bodies = [c.request.body["n"] for c in proxy_client.captured_calls]
    assert sequences == [1, 2, 3]
    assert bodies == [1, 2, 3]


def test_captured_headers_are_redacted_on_disk_representation(proxy_client):
    proxy_client.post("/v1/echo", json={}, headers={"authorization": "Bearer super-secret-token"})

    call = proxy_client.captured_calls[0]
    assert call.request.headers["authorization"] == "Bearer [REDACTED]"
