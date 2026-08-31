"""Tests for codex_probe.headers: passthrough, auth injection, redaction."""

from __future__ import annotations

from codex_probe.config import BackendConfig
from codex_probe.headers import (
    apply_backend_auth,
    filter_forward_headers,
    redact_headers_for_log,
)


def _backend(**overrides) -> BackendConfig:
    fields = dict(
        name="openai",
        base_url="https://api.openai.com/v1",
        wire_api="responses",
        api_key_env=None,
    )
    fields.update(overrides)
    return BackendConfig(**fields)


def test_filter_forward_headers_drops_hop_by_hop():
    headers = {
        "connection": "keep-alive",
        "keep-alive": "timeout=5",
        "transfer-encoding": "chunked",
        "upgrade": "h2c",
        "content-type": "application/json",
    }
    result = filter_forward_headers(headers)
    assert result == {"content-type": "application/json"}


def test_filter_forward_headers_drops_host_and_content_length():
    headers = {"host": "127.0.0.1:8135", "content-length": "42", "accept": "*/*"}
    result = filter_forward_headers(headers)
    assert result == {"accept": "*/*"}


def test_filter_forward_headers_is_case_insensitive():
    headers = {"Connection": "close", "Host": "example.com", "X-Custom": "keep-me"}
    result = filter_forward_headers(headers)
    assert result == {"X-Custom": "keep-me"}


def test_filter_forward_headers_preserves_meaningful_headers():
    headers = {
        "authorization": "Bearer sk-abc",
        "content-type": "application/json",
        "accept": "text/event-stream",
    }
    result = filter_forward_headers(headers)
    assert result == headers


def test_apply_backend_auth_injects_from_env(monkeypatch):
    monkeypatch.setenv("TEST_API_KEY", "sk-real-key")
    backend = _backend(api_key_env="TEST_API_KEY")

    result = apply_backend_auth({"authorization": "Bearer whatever-codex-sent"}, backend)

    assert result["authorization"] == "Bearer sk-real-key"


def test_apply_backend_auth_no_api_key_env_leaves_headers_untouched():
    backend = _backend(api_key_env=None)
    original = {"authorization": "Bearer whatever-codex-sent"}

    result = apply_backend_auth(original, backend)

    assert result == original


def test_apply_backend_auth_missing_env_var_leaves_headers_untouched(monkeypatch):
    monkeypatch.delenv("MISSING_KEY", raising=False)
    backend = _backend(api_key_env="MISSING_KEY")
    original = {"authorization": "Bearer whatever-codex-sent"}

    result = apply_backend_auth(original, backend)

    assert result == original


def test_apply_backend_auth_does_not_mutate_input(monkeypatch):
    monkeypatch.setenv("TEST_API_KEY", "sk-real-key")
    backend = _backend(api_key_env="TEST_API_KEY")
    original = {"authorization": "Bearer original"}

    apply_backend_auth(original, backend)

    assert original == {"authorization": "Bearer original"}


def test_redact_headers_for_log_redacts_bearer_token():
    result = redact_headers_for_log({"authorization": "Bearer sk-super-secret"})
    assert result["authorization"] == "Bearer [REDACTED]"


def test_redact_headers_for_log_redacts_non_bearer_api_key():
    result = redact_headers_for_log({"x-api-key": "sk-super-secret"})
    assert result["x-api-key"] == "[REDACTED]"


def test_redact_headers_for_log_preserves_non_sensitive_headers():
    headers = {"content-type": "application/json", "accept": "text/event-stream"}
    assert redact_headers_for_log(headers) == headers


def test_redact_headers_for_log_does_not_mutate_input():
    original = {"authorization": "Bearer sk-secret"}
    redact_headers_for_log(original)
    assert original == {"authorization": "Bearer sk-secret"}
