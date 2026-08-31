"""End-to-end lifecycle tests for ProxyRecorder, the assignment's required API.

Unlike the passthrough/streaming tests (which wire the proxy app
directly to an in-process mock backend via `httpx.ASGITransport`), these
exercise `ProxyRecorder`'s actual job: binding a real local TCP port,
running a real background Uvicorn server, and talking to a real (locally
hosted) backend over a real socket -- because that is exactly what Codex
itself will do.
"""

from __future__ import annotations

import json

import httpx
import pytest

from codex_probe.errors import ConfigError
from codex_probe.recorder import ProxyRecorder


def _config(base_url: str, log_dir) -> dict:
    return {
        "listen_host": "127.0.0.1",
        "listen_port": 0,  # let the OS pick a free port; avoids test collisions
        "backend": {
            "name": "mock-backend",
            "base_url": base_url,
            "wire_api": "chat_completions",
        },
        "log_dir": str(log_dir),
    }


def test_start_returns_a_local_v1_endpoint(live_mock_backend, tmp_path):
    recorder = ProxyRecorder(_config(live_mock_backend["base_url"] + "/v1", tmp_path))
    endpoint = recorder.start()
    try:
        assert endpoint.startswith("http://127.0.0.1:")
        assert endpoint.endswith("/v1")
    finally:
        recorder.stop()


def test_recorder_forwards_requests_and_captures_them(live_mock_backend, tmp_path):
    recorder = ProxyRecorder(_config(live_mock_backend["base_url"] + "/v1", tmp_path))
    endpoint = recorder.start()
    try:
        response = httpx.post(f"{endpoint}/echo", json={"hello": "world"}, timeout=5)
        assert response.status_code == 200
        assert response.json() == {"echo": {"hello": "world"}}
    finally:
        calls = recorder.stop()

    assert len(calls) == 1
    assert calls[0]["request"]["body"] == {"hello": "world"}
    assert calls[0]["response"]["body"] == {"echo": {"hello": "world"}}


def test_recorder_writes_session_logs_to_disk(live_mock_backend, tmp_path):
    recorder = ProxyRecorder(_config(live_mock_backend["base_url"] + "/v1", tmp_path))
    endpoint = recorder.start()
    httpx.post(f"{endpoint}/echo", json={"a": 1}, timeout=5)
    recorder.stop()

    session_dirs = list(tmp_path.iterdir())
    assert len(session_dirs) == 1
    session_dir = session_dirs[0]
    assert session_dir.name == recorder.session_id
    assert (session_dir / "metadata.json").exists()
    assert (session_dir / "calls.jsonl").exists()

    metadata = json.loads((session_dir / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["backend"] == "mock-backend"
    assert metadata["wire_api"] == "chat_completions"
    assert metadata["ended_at"] is not None


def test_stop_returns_calls_in_order(live_mock_backend, tmp_path):
    recorder = ProxyRecorder(_config(live_mock_backend["base_url"] + "/v1", tmp_path))
    endpoint = recorder.start()
    for i in range(3):
        httpx.post(f"{endpoint}/echo", json={"n": i}, timeout=5)
    calls = recorder.stop()

    assert [c["sequence"] for c in calls] == [1, 2, 3]
    assert [c["request"]["body"]["n"] for c in calls] == [0, 1, 2]


def test_lifecycle_repeats_cleanly_across_fresh_instances(live_mock_backend, tmp_path):
    for _ in range(3):
        recorder = ProxyRecorder(_config(live_mock_backend["base_url"] + "/v1", tmp_path))
        endpoint = recorder.start()
        response = httpx.get(f"{endpoint}/models", timeout=5)
        assert response.status_code == 200
        calls = recorder.stop()
        assert len(calls) == 1


def test_starting_twice_raises(live_mock_backend, tmp_path):
    recorder = ProxyRecorder(_config(live_mock_backend["base_url"] + "/v1", tmp_path))
    recorder.start()
    try:
        with pytest.raises(RuntimeError):
            recorder.start()
    finally:
        recorder.stop()


def test_stopping_twice_raises(live_mock_backend, tmp_path):
    recorder = ProxyRecorder(_config(live_mock_backend["base_url"] + "/v1", tmp_path))
    recorder.start()
    recorder.stop()
    with pytest.raises(RuntimeError):
        recorder.stop()


def test_stop_without_start_raises(tmp_path):
    recorder = ProxyRecorder(_config("http://127.0.0.1:1/v1", tmp_path))
    with pytest.raises(RuntimeError):
        recorder.stop()


def test_invalid_config_raises_config_error(tmp_path):
    with pytest.raises(ConfigError):
        ProxyRecorder({"listen_port": 8135})
