"""Command-line interface for CodexProbe.

    codex-probe --config examples/openai_backend.json

Runs a `ProxyRecorder` in the foreground until interrupted (Ctrl+C),
printing what Codex needs to know (the local endpoint, session id, log
path) and a summary of what was captured on exit. This makes the proxy
usable on its own, without writing a Python script around it.
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import time
from pathlib import Path

from codex_probe.errors import CodexProbeError
from codex_probe.recorder import ProxyRecorder

_POLL_INTERVAL_SECONDS = 0.2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="codex-probe",
        description="Transparent LLM call recorder and backend swap harness for Codex CLI.",
    )
    parser.add_argument(
        "--config",
        required=True,
        type=Path,
        help="Path to a JSON recorder configuration file (see examples/).",
    )
    return parser


def _load_config_file(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        raw_config = _load_config_file(args.config)
    except FileNotFoundError:
        print(f"error: config file not found: {args.config}", file=sys.stderr)
        return 1
    except json.JSONDecodeError as exc:
        print(f"error: {args.config} is not valid JSON: {exc}", file=sys.stderr)
        return 1

    try:
        recorder = ProxyRecorder(raw_config)
        endpoint = recorder.start()
    except CodexProbeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print("CodexProbe")
    print(f"Session:   {recorder.session_id}")
    print(f"Backend:   {recorder.config.backend.name}")
    print(f"Wire API:  {recorder.config.backend.wire_api}")
    print(f"Listening: {endpoint}")
    print(f"Logs:      {recorder.calls_path}")
    print()
    print("Point Codex's model_provider.base_url at the endpoint above, then run Codex.")
    print("Press Ctrl+C to stop recording.")
    sys.stdout.flush()

    stop_requested = False

    def _handle_sigint(signum, frame) -> None:  # noqa: ARG001 - signal handler signature
        nonlocal stop_requested
        stop_requested = True

    previous_handler = signal.signal(signal.SIGINT, _handle_sigint)
    try:
        while not stop_requested:
            time.sleep(_POLL_INTERVAL_SECONDS)
    finally:
        signal.signal(signal.SIGINT, previous_handler)
        calls = recorder.stop()
        print(f"\nStopped. Recorded {len(calls)} call(s) to {recorder.calls_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
