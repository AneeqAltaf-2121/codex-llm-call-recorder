# Architecture

## The problem

Codex CLI talks to an LLM backend over plain OpenAI-compatible HTTP. To
study or reproduce that traffic (the full prompts, tool schemas, and
responses, not summaries), something has to sit on that wire and record
it, without changing what Codex itself does or sees.

Two ways to get there:

1. **Modify Codex's Rust core** to log every request/response itself.
2. **Put a transparent reverse proxy in front of the backend**, and point
   Codex's existing `model_provider.base_url` configuration at it.

CodexProbe is (2). Codex already supports configuring arbitrary
OpenAI-compatible providers (see `docs/codex-configuration.md`); that
configuration surface is the extension point this project uses, so Codex
itself is never touched, rebuilt, or forked. This also means CodexProbe
works with *any* HTTP client that speaks the same protocol, not just
Codex.

## Topology

```
                    Codex CLI
                        |
                        | OpenAI-compatible HTTP
                        v
              +--------------------+
              |    CodexProbe      |
              |                    |
              |  Reverse Proxy     |
              |  Stream Recorder   |
              |  Session Logger    |
              +---------+----------+
                        |
                  configured backend
                        |
             +----------+-----------+
             v                      v
      OpenAI backend         Local Qwen backend
                              (Ollama / vLLM)
```

Codex is configured once, with a `base_url` pointing at CodexProbe
instead of a real backend. CodexProbe forwards every request to whichever
backend its own configuration names, and writes a complete record of
each call to disk. Which backend that is, and nothing else, changes
when you swap from OpenAI to a local Qwen model (see
`docs/backend-swap.md`).

## Module boundaries

Each module has one job and depends only on the modules below it in this
list. `proxy.py` orchestrates; everything else is a narrow, independently
testable unit:

| Module              | Responsibility                                                            |
| -------------------- | -------------------------------------------------------------------------- |
| `config.py`          | Validate a raw config dict into a typed `RecorderConfig` (the config-only backend-swap boundary). |
| `models.py`          | The `CapturedCall` schema every recorded exchange is normalized into.     |
| `errors.py`          | Shared exception types (`ConfigError`, `UpstreamError`).                   |
| `headers.py`         | Which headers get forwarded, how backend auth is injected, how headers are redacted for logs. |
| `transport.py`       | A persistent `httpx.AsyncClient` bound to one backend. Knows nothing about Codex, sessions, or logs. |
| `streaming.py`       | Forwards SSE chunks to the client the instant they arrive while capturing them for the log. |
| `proxy.py`           | The FastAPI catch-all app: routes every method/path, decides streaming vs. buffered, and reports finished calls. |
| `logging_store.py`   | Persists calls to a per-session `calls.jsonl` + `metadata.json`.           |
| `recorder.py`        | `ProxyRecorder`: the public `start()`/`stop()` lifecycle API, wiring everything above together. |
| `cli.py`             | `codex-probe --config ...`: runs a recorder session from the command line. |

## Why async networking

Codex issues requests and expects a response (or a live stream) back on
the same connection with no added latency it can attribute to the proxy.
`httpx.AsyncClient` gives CodexProbe non-blocking I/O and connection
pooling to the backend, and FastAPI/Starlette (built on ASGI) gives it a
non-blocking server for Codex's own connection, so one slow backend
call doesn't block a second, concurrent one, and the added latency from
CodexProbe itself is negligible (process the request, forward it,
process the response).

## Streaming, in more detail

See `docs/log-format.md` for the schema, but the property worth calling
out here: CodexProbe never buffers a streamed response before relaying
it. `streaming.py` forwards each chunk to Codex the instant it arrives
and appends it to an in-memory buffer at the same time; only once the
stream ends is the buffer reassembled into a complete body and logged.
Getting this wrong (buffer everything, *then* forward) would silently
turn every streaming response into a non-streaming one from Codex's
point of view: exactly the kind of behavior change this project
promises not to introduce.

## What CodexProbe deliberately does not do

- **No translation between wire protocols.** If the backend speaks
  `chat_completions` and Codex is configured for `responses`, CodexProbe
  will faithfully forward a request Codex intended for one protocol to a
  backend expecting the other, and record whatever the backend does with
  it (an error, most likely). That mismatch is a *configuration* error
  for the person running the experiment to avoid; see
  `docs/codex-configuration.md`.
- **No response modification.** Status code, headers (aside from the
  hop-by-hop headers no proxy should forward, see `headers.py`), and body
  are relayed unchanged.
- **No sampling, truncation, or summarization of logs.** See
  `docs/log-format.md`.
