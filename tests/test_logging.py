"""Tests for the captured-call schema (codex_probe.models).

Logging-store persistence tests (session directories, calls.jsonl,
metadata.json) are added alongside these in Phase 8.
"""

from __future__ import annotations

import json

from codex_probe.models import (
    BackendInfo,
    CapturedCall,
    RequestRecord,
    ResponseRecord,
    decode_body,
    utc_timestamp,
)


def _sample_call(**overrides) -> CapturedCall:
    fields = dict(
        call_id="call-1",
        sequence=1,
        session_id="session-1",
        timestamp=utc_timestamp(),
        backend=BackendInfo(name="openai", base_url="https://api.openai.com/v1", wire_api="responses"),
        request=RequestRecord(
            method="POST",
            path="/v1/responses",
            headers={"content-type": "application/json"},
            body={"model": "gpt-5", "input": [{"role": "system", "content": "full instructions"}]},
        ),
        response=ResponseRecord(
            status_code=200,
            headers={"content-type": "application/json"},
            body={"output": "the complete response text"},
        ),
        streaming=False,
        latency_ms=824.3,
    )
    fields.update(overrides)
    return CapturedCall(**fields)


def test_captured_call_round_trips_through_json():
    call = _sample_call()
    dumped = json.loads(call.model_dump_json())

    assert dumped["call_id"] == "call-1"
    assert dumped["sequence"] == 1
    assert dumped["backend"]["name"] == "openai"
    assert dumped["request"]["body"]["input"][0]["content"] == "full instructions"
    assert dumped["response"]["body"]["output"] == "the complete response text"
    assert dumped["streaming"] is False
    assert dumped["latency_ms"] == 824.3


def test_captured_call_preserves_full_request_body_not_a_preview():
    long_system_prompt = "You are Codex. " + ("Detailed rule. " * 500)
    long_tool_history = [{"role": "tool", "content": f"tool output {i}"} for i in range(200)]
    call = _sample_call(
        request=RequestRecord(
            method="POST",
            path="/v1/responses",
            body={"instructions": long_system_prompt, "history": long_tool_history},
        )
    )

    stored = call.model_dump()["request"]["body"]

    # The full body must be present verbatim -- no truncation, no summary.
    assert stored["instructions"] == long_system_prompt
    assert len(stored["history"]) == 200
    assert stored["history"][199]["content"] == "tool output 199"


def test_captured_call_preserves_full_response_body():
    long_output = "chunk " * 5000
    call = _sample_call(response=ResponseRecord(status_code=200, body={"output_text": long_output}))

    stored = call.model_dump()["response"]["body"]

    assert stored["output_text"] == long_output


def test_decode_body_parses_json():
    assert decode_body(b'{"a": 1, "b": [1, 2, 3]}') == {"a": 1, "b": [1, 2, 3]}


def test_decode_body_falls_back_to_text_for_non_json():
    assert decode_body(b"data: some sse chunk\n\n") == "data: some sse chunk\n\n"


def test_decode_body_handles_empty_bytes():
    assert decode_body(b"") is None


def test_utc_timestamp_is_iso8601_utc():
    ts = utc_timestamp()
    assert ts.endswith("Z")
    # Must be parseable as ISO-8601 once the trailing Z is normalized back.
    from datetime import datetime

    datetime.fromisoformat(ts.replace("Z", "+00:00"))


def test_call_schema_rejects_unknown_fields():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        CapturedCall(
            call_id="x",
            sequence=1,
            session_id="s",
            timestamp=utc_timestamp(),
            backend=BackendInfo(name="openai", base_url="https://api.openai.com/v1", wire_api="responses"),
            request=RequestRecord(method="POST", path="/v1/responses"),
            response=ResponseRecord(status_code=200),
            latency_ms=1.0,
            unexpected_field="nope",
        )
