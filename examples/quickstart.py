"""Minimal example of the ProxyRecorder Python API.

    python examples/quickstart.py examples/openai_backend.json

Starts a recorder session, prints the endpoint to point Codex at, waits
for you to run Codex (or send it requests) in another terminal, then
stops on Enter and prints a summary of everything captured.

This mirrors what `codex-probe --config ...` does under the hood, but
shown as plain library code so it's obvious there's no magic: `start()`
gives you a URL, `stop()` gives you back the calls.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from codex_probe import ProxyRecorder


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} <config.json>", file=sys.stderr)
        return 1

    config_path = Path(sys.argv[1])
    config = json.loads(config_path.read_text(encoding="utf-8"))

    recorder = ProxyRecorder(config)
    endpoint = recorder.start()

    print(f"CodexProbe listening at {endpoint}")
    print(f"Session:  {recorder.session_id}")
    print(f"Logs:     {recorder.calls_path}")
    print()
    print("Point Codex's model_provider.base_url at the endpoint above,")
    print("run Codex against it, then press Enter here to stop recording.")
    input()

    calls = recorder.stop()
    print(f"\nRecorded {len(calls)} call(s):")
    for call in calls:
        print(
            f"  #{call['sequence']} {call['request']['method']} {call['request']['path']} "
            f"-> {call['response']['status_code']} ({call['latency_ms']:.1f} ms, "
            f"streaming={call['streaming']})"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
