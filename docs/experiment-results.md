# Experiment results

## Scope and honesty note

This document has two parts, and they should not be confused with each
other:

1. **What was actually verified in the environment CodexProbe was built
   in** -- the automated test suite (`pytest`, 67 tests) and manual
   end-to-end smoke tests against real local backends over real TCP
   sockets. This is real, reproducible evidence that passthrough fidelity,
   streaming, header handling, session logging, and the `ProxyRecorder`
   lifecycle all work correctly.
2. **A live Codex-vs-OpenAI and Codex-vs-Qwen comparison**, as the
   assignment's research framing intends. This requires a real OpenAI API
   key (which costs money per call) and either a local Ollama/vLLM
   install with a multi-gigabyte model download, or a GPU machine --
   neither of which is available in the sandboxed environment this
   project was built in. Rather than fabricate numbers, this section is
   a filled-in methodology and a template for the actual results, which
   the next person running this repository (with their own API key
   and/or local model) can populate by following `docs/backend-swap.md`
   and pasting in what CodexProbe actually recorded.

Presenting invented latency or call-count numbers as if they came from a
real run would defeat the entire point of building a recorder to get
*faithful* data in the first place.

## What was verified

- **Passthrough fidelity** (`tests/test_passthrough.py`): request bodies,
  headers, query strings, status codes, and response bodies all arrive
  unchanged, including a config-supplied backend API key overriding
  whatever Codex sent.
- **Streaming** (`tests/test_streaming.py`): SSE events are forwarded
  live, in order, with the exact reassembled body available in the log
  record -- byte-for-byte identical to what the client received.
- **Session logging** (`tests/test_logging.py`): `metadata.json` and
  `calls.jsonl` are created correctly, calls are appended one JSON object
  per line in order, and everything survives being read back after the
  session (and the log store) is closed.
- **`ProxyRecorder` lifecycle** (`tests/test_recorder.py`): `start()`
  binds a real local port and returns a working `.../v1` endpoint,
  `stop()` returns every captured call in order, and the full
  `ProxyRecorder(...).start() -> stop()` cycle was run repeatedly against
  a real backend over a real socket, including error paths (double
  start, stop-before-start, double stop, invalid config).
- **CLI smoke test**: `codex-probe --config <file>` was run manually
  against a real local backend process; it printed the expected session
  banner, proxied a live request correctly, and produced a populated
  `logs/<session>/` directory once stopped.

Run these yourself:

```bash
pip install -e ".[dev]"
pytest -q --cov=codex_probe --cov-report=term-missing
ruff check .
mypy src/codex_probe
```

## Methodology for the live Codex comparison

### Task

A fixed, small coding task, run once against each backend so the two
sessions are comparable:

```
codex exec "Create a Python function that reverses a string and add a unit test."
```

### Procedure

1. `codex-probe --config examples/openai_backend.json`, set Codex's
   config to `wire_api = "responses"`, run the task, Ctrl+C.
2. Note the session id CodexProbe printed; call it `SESSION_OPENAI`.
3. Set up a local Qwen backend (`docs/backend-swap.md`) --
   `qwen2.5-coder:7b` via Ollama is the recommended starting point.
4. `codex-probe --config examples/qwen_ollama_backend.json`, set Codex's
   config to `wire_api = "chat_completions"`, run the *same* task,
   Ctrl+C.
5. Note that session id; call it `SESSION_QWEN`.
6. Compare `logs/SESSION_OPENAI/` and `logs/SESSION_QWEN/`.

### Metrics to pull from `calls.jsonl`

Each of these is a straightforward `jq`/Python pass over
`logs/<session>/calls.jsonl`:

- **Number of LLM calls** -- `len(calls)`, or count of distinct
  `sequence` values.
- **Latency** -- `latency_ms` per call; report min/median/max, since
  local CPU-bound inference is typically far slower and far more
  variable than a hosted API.
- **Tool-call behavior** -- for each call, whether `request.body` includes
  a `tools` array, and whether `response.body` contains a corresponding
  tool/function call the model actually emitted (vs. plain text where a
  tool call was expected).
- **Streaming** -- fraction of calls with `"streaming": true`, and
  whether the backend produced any streamed output at all (some local
  servers default to non-streaming responses even when Codex requests
  streaming).
- **Success/failure** -- fraction of calls with `response.status_code
  == 200` vs. errors, and whether the *task* completed (Codex reported
  success, produced working code) independent of individual call status.

## Results template

Fill this in after running the procedure above.

### Experiment 1 -- OpenAI (`examples/openai_backend.json`)

| Metric                  | Value |
| ------------------------ | ----- |
| Session id               |       |
| Number of LLM calls      |       |
| Latency (min/median/max) |       |
| Tool calls observed      |       |
| Streaming used           |       |
| Task outcome             |       |

Notes / anomalies:

### Experiment 2 -- Local Qwen (`examples/qwen_ollama_backend.json`)

| Metric                  | Value |
| ------------------------ | ----- |
| Session id               |       |
| Number of LLM calls      |       |
| Latency (min/median/max) |       |
| Tool calls observed      |       |
| Streaming used           |       |
| Task outcome             |       |

Notes / anomalies:

### Comparison

- Did the two backends need the same number of calls to complete the
  task, or did one need more turns (e.g. because tool calls failed and
  Codex retried)?
- How did latency per call compare? Local CPU inference is expected to
  be substantially slower than a hosted API; a GPU-backed vLLM server
  narrows that gap.
- Did Qwen's tool-call output match the shape Codex expected? If not,
  what did the malformed output actually look like (quote from
  `calls.jsonl`)?
- Any other qualitative differences in prompt/response structure worth
  noting (e.g. reasoning verbosity, refusal behavior, formatting).

## Limitations of this comparison

- A single task run once per backend is not statistically meaningful --
  it demonstrates the harness works, not a robust performance claim.
  Repeating the task multiple times per backend (and reporting variance)
  would be a natural next step.
- Local model quality/latency depends heavily on the host machine
  (CPU/GPU, quantization level, concurrent load); results are only
  representative of the specific machine they were measured on.
- `latency_ms` measures CodexProbe's own vantage point (request sent
  upstream to response ready), not Codex's end-to-end perceived latency,
  which also includes Codex's own processing time.
