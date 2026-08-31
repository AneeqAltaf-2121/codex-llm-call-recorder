"""Data model for one captured LLM call.

This is the schema every recorded Codex<->backend exchange is normalized
into before being written to disk (see :mod:`codex_probe.logging_store`).

The assignment's core research requirement is that logs are a **faithful,
complete** record: the full system/developer instructions, the full
conversation and tool-call history, the full tool schema, and the full
response. Nothing here should ever store a truncated preview *instead of*
the real payload. A caller is free to add a preview field alongside the
full body, but ``request.body`` / ``response.body`` below are always the
complete, decoded payload.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


class BackendInfo(BaseModel):
    """Identifies which upstream backend a call was made against."""

    name: str
    base_url: str
    wire_api: str

    model_config = {"extra": "forbid"}


class RequestRecord(BaseModel):
    """The complete outgoing request, as Codex sent it."""

    method: str
    path: str
    headers: dict[str, str] = Field(default_factory=dict)
    #: Full JSON body (parsed) or full decoded text; never a preview.
    body: Any = None

    model_config = {"extra": "forbid"}


class ResponseRecord(BaseModel):
    """The complete response returned by the backend, streamed or not."""

    status_code: int
    headers: dict[str, str] = Field(default_factory=dict)
    #: Full JSON body (parsed), full decoded text, or the fully
    #: reassembled text of a streamed response; never a preview.
    body: Any = None

    model_config = {"extra": "forbid"}


class CapturedCall(BaseModel):
    """One complete, self-contained record of a single LLM call."""

    call_id: str
    #: 1-based position of this call within its session, in call order.
    sequence: int
    session_id: str
    #: ISO-8601 UTC timestamp of when the call started.
    timestamp: str
    backend: BackendInfo
    request: RequestRecord
    response: ResponseRecord
    #: Whether the response was served as a stream (SSE) rather than a
    #: single buffered body.
    streaming: bool = False
    latency_ms: float

    model_config = {"extra": "forbid"}


def utc_timestamp() -> str:
    """Return the current time as an ISO-8601 UTC timestamp, e.g. ``...Z``."""
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def decode_body(raw: bytes) -> Any:
    """Decode a raw HTTP body for storage in a log record.

    JSON bodies (the overwhelming majority of OpenAI-compatible traffic)
    are stored as parsed JSON so logs are human-readable and greppable.
    Anything else is stored as decoded text. The body is never truncated
    and never replaced with a summary. Callers that also want a short
    preview should compute one separately, alongside this full value.
    """
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        pass
    return raw.decode("utf-8", errors="replace")
