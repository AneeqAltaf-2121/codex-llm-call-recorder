"""Persistent, session-organized logging of captured calls.

Each recorder run gets its own session directory::

    logs/
    └── 20260830_204503_a6b31d/
        ├── metadata.json
        └── calls.jsonl

``calls.jsonl`` holds one JSON object per line, one per captured call, in
call order -- so a researcher (or a script) can replay a session call by
call: prompt 1, response 1, tool action, prompt 2, response 2, ...
``metadata.json`` records session-level facts (which backend and wire API
were in effect, when the session started and ended) that don't belong on
every individual call record.
"""

from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime
from pathlib import Path

from codex_probe.models import CapturedCall


def new_session_id(now: datetime | None = None) -> str:
    """Generate a session id such as ``20260830_204503_a6b31d``.

    Timestamp-prefixed so sessions sort chronologically on disk;
    suffixed with a short random token so two sessions starting in the
    same second (e.g. two recorder instances in one test run) never
    collide.
    """
    now = now or datetime.now(UTC)
    return f"{now.strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(3)}"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class SessionLogStore:
    """Owns the on-disk directory for one recorder session.

    Created once at the start of a session (see
    :class:`~codex_probe.recorder.ProxyRecorder`), fed one
    :class:`~codex_probe.models.CapturedCall` at a time as requests
    complete, and closed once at the end of the session.
    """

    def __init__(self, log_dir: Path, session_id: str, backend_name: str, wire_api: str) -> None:
        self.session_id = session_id
        self.session_dir = Path(log_dir) / session_id
        self.session_dir.mkdir(parents=True, exist_ok=True)

        self.calls_path = self.session_dir / "calls.jsonl"
        self.metadata_path = self.session_dir / "metadata.json"

        self._backend_name = backend_name
        self._wire_api = wire_api
        self._started_at = _now_iso()
        self._closed = False

        # Opened once and kept open for the life of the session: append
        # mode plus an immediate flush per write keeps every call durable
        # on disk as soon as it is captured, without reopening the file
        # (and re-paying filesystem overhead) on every request.
        self._calls_file = self.calls_path.open("a", encoding="utf-8")
        self._write_metadata(ended_at=None)

    def record_call(self, call: CapturedCall) -> None:
        """Append one captured call to ``calls.jsonl`` as a single JSON line."""
        if self._closed:
            raise RuntimeError(f"session '{self.session_id}' is already closed")
        self._calls_file.write(call.model_dump_json())
        self._calls_file.write("\n")
        self._calls_file.flush()

    def read_calls(self) -> list[dict]:
        """Read back every call recorded so far, in call order."""
        if not self.calls_path.exists():
            return []
        calls: list[dict] = []
        with self.calls_path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    calls.append(json.loads(line))
        return calls

    def close(self) -> list[dict]:
        """Finish the session: stamp ``ended_at`` and return every call recorded.

        Safe to call more than once; subsequent calls just re-read the
        (already closed) log file.
        """
        if not self._closed:
            self._calls_file.close()
            self._write_metadata(ended_at=_now_iso())
            self._closed = True
        return self.read_calls()

    def _write_metadata(self, *, ended_at: str | None) -> None:
        metadata = {
            "session_id": self.session_id,
            "started_at": self._started_at,
            "ended_at": ended_at,
            "backend": self._backend_name,
            "wire_api": self._wire_api,
        }
        self.metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
