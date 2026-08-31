"""Tests for the captured-call schema and the session log store.

Covers `codex_probe.models` (the schema every call is normalized into)
and `codex_probe.logging_store` (session directories, calls.jsonl,
metadata.json).
"""

from __future__ import annotations

import json

from codex_probe.logging_store import SessionLogStore, new_session_id
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


# --- SessionLogStore ---------------------------------------------------


def test_new_session_id_matches_expected_format():
    session_id = new_session_id()
    date_part, time_part, suffix = session_id.split("_")
    assert len(date_part) == 8
    assert len(time_part) == 6
    assert len(suffix) == 6  # secrets.token_hex(3)


def test_new_session_id_is_unique_even_for_the_same_timestamp():
    from datetime import datetime, timezone

    now = datetime(2026, 8, 30, 20, 45, 3, tzinfo=timezone.utc)
    first = new_session_id(now)
    second = new_session_id(now)
    assert first != second
    assert first.startswith("20260830_204503_")
    assert second.startswith("20260830_204503_")


def test_session_store_creates_directory_and_metadata(tmp_path):
    store = SessionLogStore(tmp_path, "session-1", backend_name="openai", wire_api="responses")

    assert store.session_dir == tmp_path / "session-1"
    assert store.session_dir.is_dir()
    assert store.metadata_path.exists()

    metadata = json.loads(store.metadata_path.read_text(encoding="utf-8"))
    assert metadata["session_id"] == "session-1"
    assert metadata["backend"] == "openai"
    assert metadata["wire_api"] == "responses"
    assert metadata["started_at"]
    assert metadata["ended_at"] is None

    store.close()


def test_record_call_appends_one_json_object_per_line(tmp_path):
    store = SessionLogStore(tmp_path, "session-2", backend_name="qwen-ollama", wire_api="chat_completions")
    calls = [_sample_call(call_id=f"call-{i}", sequence=i) for i in (1, 2, 3)]

    for call in calls:
        store.record_call(call)
    store.close()

    lines = store.calls_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3
    parsed = [json.loads(line) for line in lines]
    assert [entry["sequence"] for entry in parsed] == [1, 2, 3]
    assert [entry["call_id"] for entry in parsed] == ["call-1", "call-2", "call-3"]


def test_read_calls_returns_calls_in_recorded_order(tmp_path):
    store = SessionLogStore(tmp_path, "session-3", backend_name="openai", wire_api="responses")
    for i in (1, 2, 3):
        store.record_call(_sample_call(call_id=f"call-{i}", sequence=i))

    calls = store.read_calls()

    assert [c["sequence"] for c in calls] == [1, 2, 3]


def test_close_stamps_ended_at_and_returns_all_calls(tmp_path):
    store = SessionLogStore(tmp_path, "session-4", backend_name="openai", wire_api="responses")
    store.record_call(_sample_call(call_id="call-1", sequence=1))

    returned_calls = store.close()

    assert len(returned_calls) == 1
    metadata = json.loads(store.metadata_path.read_text(encoding="utf-8"))
    assert metadata["ended_at"] is not None


def test_record_call_after_close_raises(tmp_path):
    import pytest

    store = SessionLogStore(tmp_path, "session-5", backend_name="openai", wire_api="responses")
    store.close()

    with pytest.raises(RuntimeError):
        store.record_call(_sample_call(call_id="call-1", sequence=1))


def test_calls_persist_on_disk_after_store_is_closed(tmp_path):
    store = SessionLogStore(tmp_path, "session-6", backend_name="openai", wire_api="responses")
    store.record_call(_sample_call(call_id="call-1", sequence=1))
    store.close()

    # A fresh read (simulating a separate process/researcher inspecting
    # the logs later) must see exactly what was recorded.
    on_disk = (tmp_path / "session-6" / "calls.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(on_disk) == 1
    assert json.loads(on_disk[0])["call_id"] == "call-1"
