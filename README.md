# CodexProbe

CodexProbe is a transparent, streaming-aware reverse proxy and recorder
for OpenAI Codex CLI. It captures the complete request and response of
every LLM interaction without modifying Codex's execution behavior, and
allows the underlying model backend to be changed through configuration
alone. CodexProbe supports recording Codex sessions against standard
OpenAI-compatible services as well as locally hosted open-source models
such as Qwen.

```
Codex CLI
   |
   v
CodexProbe
   |
   v
OpenAI / Qwen / any OpenAI-compatible backend
```

Codex is never modified. It is simply configured so its
`model_provider.base_url` points at CodexProbe instead of at a backend
directly, the same extension point Codex already exposes for using any
OpenAI-compatible provider.

## Why CodexProbe exists

Studying or reproducing what an agentic coding tool like Codex actually
sends to an LLM (the full system/developer instructions, the full
conversation and tool-call history, the full tool schema, and the full
response) requires something to sit on that wire and record it
faithfully. Rewriting Codex's Rust core to log itself is invasive and
backend-specific; a transparent local proxy is not. It also makes a
second question answerable directly: what actually changes in an agent's
behavior when you swap the model behind it from a hosted frontier model
to a small, locally hosted open-weight model, with the harness that
records both runs staying byte-for-byte identical. See
[`docs/architecture.md`](docs/architecture.md) for the full design
rationale.

## Architecture

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

Details, module boundaries, and design trade-offs:
[`docs/architecture.md`](docs/architecture.md).

## Features

- **Faithful passthrough**: method, path, query string, headers, and
  body are forwarded and returned unchanged; Codex behaves identically
  whether CodexProbe is in the path or not.
- **Live streaming, fully captured**: Server-Sent Events are forwarded
  to Codex chunk-by-chunk as they arrive, never buffered, while being
  simultaneously reassembled for the log.
- **Complete, unabridged logging**: full instructions, full tool
  schemas, full conversation history, full responses. Never a preview or
  a truncated summary in place of the real payload.
- **Config-only backend swap**: switch between OpenAI, Ollama, vLLM, or
  any OpenAI-compatible server by editing a JSON config file. Zero
  changes to `src/codex_probe/`.
- **Per-session logs**: each recorder run gets its own
  `logs/<session-id>/{metadata.json,calls.jsonl}`, safe to replay call by
  call.
- **Header redaction on disk**: API keys never appear in plaintext in a
  saved log, even though CodexProbe itself still sees and forwards them.
- **Installable package + CLI**: `pip install -e .` gives you both the
  `ProxyRecorder` Python API and a `codex-probe` command.

## Installation

Requires Python 3.11+.

```bash
git clone https://github.com/AneeqAltaf-2121/codex-llm-call-recorder.git
cd codex-llm-call-recorder
python -m venv .venv
source .venv/bin/activate   # .venv\Scripts\activate on Windows
pip install -e ".[dev]"
```

## Quick start

```bash
codex-probe --config examples/openai_backend.json
```

```
CodexProbe
Session:   20260830_204503_a6b31d
Backend:   openai
Wire API:  responses
Listening: http://127.0.0.1:8135/v1
Logs:      logs/20260830_204503_a6b31d/calls.jsonl

Point Codex's model_provider.base_url at the endpoint above, then run Codex.
Press Ctrl+C to stop recording.
```

Point Codex at that endpoint (see [Codex configuration](#codex-configuration)
below), run it, then Ctrl+C to stop; CodexProbe prints how many calls
it recorded and where.

## Python API

The assignment's required interface:

```python
from codex_probe import ProxyRecorder

recorder = ProxyRecorder({
    "listen_host": "127.0.0.1",
    "listen_port": 8135,
    "backend": {
        "name": "openai",
        "base_url": "https://api.openai.com/v1",
        "wire_api": "responses",
        "api_key_env": "OPENAI_API_KEY",
    },
    "log_dir": "./logs",
})

endpoint = recorder.start()   # -> "http://127.0.0.1:8135/v1"

# ... point Codex at `endpoint` and run it ...

calls = recorder.stop()       # -> list[dict], every call captured, in order
```

A runnable version of this lives at
[`examples/quickstart.py`](examples/quickstart.py):

```bash
python examples/quickstart.py examples/openai_backend.json
```

## Codex configuration

Add a provider to `~/.codex/config.toml` (full example:
[`examples/codex-config-example.toml`](examples/codex-config-example.toml)):

```toml
[model_providers.codex_probe]
name = "CodexProbe"
base_url = "http://127.0.0.1:8135/v1"
wire_api = "responses"
env_key = "OPENAI_API_KEY"

model_provider = "codex_probe"
model = "gpt-5-codex"
```

`base_url` must match whatever CodexProbe printed on startup; `wire_api`
must match the `wire_api` in CodexProbe's *own* backend config (CodexProbe
does not translate between the Responses API and Chat Completions; see
[Responses API vs Chat Completions](#responses-api-vs-chat-completions)).
Full walkthrough: [`docs/codex-configuration.md`](docs/codex-configuration.md).

## Recording an OpenAI session

```bash
codex-probe --config examples/openai_backend.json
# in another terminal, with Codex configured as above:
codex exec "Create a Python function that reverses a string and add a unit test."
# back in the CodexProbe terminal: Ctrl+C
```

`examples/openai_backend.json` reads its API key from the
`OPENAI_API_KEY` environment variable (see
[`.env.example`](.env.example); never commit a real key).

## Inspecting logs

```
logs/20260830_204503_a6b31d/
├── metadata.json   # session-level: backend, wire_api, started_at, ended_at
└── calls.jsonl      # one complete call per line, in order
```

```bash
cat logs/*/metadata.json | python -m json.tool
python -c "
import json
with open('logs/20260830_204503_a6b31d/calls.jsonl') as f:
    for line in f:
        call = json.loads(line)
        print(call['sequence'], call['request']['path'], call['response']['status_code'], call['latency_ms'])
"
```

Full field-by-field reference: [`docs/log-format.md`](docs/log-format.md).

## Running Qwen locally

**Ollama (recommended on a laptop):**

```bash
ollama pull qwen2.5-coder:7b
ollama serve
```

Approximate requirements: ~8 GB disk for the 7B quantized model, 16 GB
system RAM as a comfortable minimum on CPU (a GPU with ~8 GB VRAM speeds
this up substantially).

**vLLM (GPU machine, more research-server-like):**

```bash
pip install vllm
python -m vllm.entrypoints.openai.api_server \
    --model Qwen/Qwen2.5-Coder-7B-Instruct --port 8000
```

Needs a CUDA GPU with roughly 16 GB+ VRAM for an unquantized 7B model.
Full setup, including Docker Compose: [`docs/backend-swap.md`](docs/backend-swap.md).

## Swapping Codex to Qwen

```bash
codex-probe --config examples/qwen_ollama_backend.json
```

Update Codex's config to `wire_api = "chat_completions"` (Ollama speaks
Chat Completions, not the Responses API), run the same task, then
compare `logs/<session>/metadata.json` against the OpenAI run:
`"backend": "qwen-ollama"` confirms the swap took effect purely through
configuration, with zero changes to CodexProbe's source. Details and
what to do if Qwen's tool calling doesn't match OpenAI's behavior:
[`docs/backend-swap.md`](docs/backend-swap.md).

## Streaming architecture

Backend chunks are forwarded to Codex the instant they arrive and
appended to an in-memory buffer at the same time:

```python
async for chunk in upstream.aiter_bytes():
    buffer.append(chunk)
    yield chunk
```

CodexProbe never buffers a full streamed response before relaying it;
doing so would silently turn every streaming response into a
non-streaming one from Codex's perspective. Once the stream ends, the
buffer is reassembled into the complete response body for the log. See
[`src/codex_probe/streaming.py`](src/codex_probe/streaming.py) and
[`docs/architecture.md`](docs/architecture.md).

## Responses API vs Chat Completions

CodexProbe is protocol-agnostic: it forwards whatever bytes Codex sent,
to whatever backend is configured, and records both. It does not
translate between OpenAI's newer Responses API (`wire_api: "responses"`,
what Codex defaults to against OpenAI) and the older Chat Completions API
(`wire_api: "chat_completions"`, what Ollama, vLLM, and most
self-hosted OpenAI-compatible servers implement). Codex's own config and
CodexProbe's backend config must agree on which protocol is in play;
see [`docs/codex-configuration.md`](docs/codex-configuration.md).

## Log schema

Every captured call is one JSON object, one line in `calls.jsonl`:

```json
{
  "call_id": "8f14e45f-ceea-4c9d-8f43-1b4b3a2b6b1a",
  "sequence": 1,
  "session_id": "20260830_204503_a6b31d",
  "timestamp": "2026-08-30T20:45:04.001Z",
  "backend": { "name": "openai", "base_url": "https://api.openai.com/v1", "wire_api": "responses" },
  "request": { "method": "POST", "path": "/v1/responses", "headers": {}, "body": {} },
  "response": { "status_code": 200, "headers": {}, "body": "..." },
  "streaming": true,
  "latency_ms": 824.3
}
```

`request.body` and `response.body` are always the **complete** payload:
full instructions, full tool schema, full conversation history, full
response. Never a truncated preview. Full field reference:
[`docs/log-format.md`](docs/log-format.md).

## Docker

```bash
docker compose up --build
docker compose exec ollama ollama pull qwen2.5-coder:7b
```

Brings up CodexProbe (`:8135`) wired to a local Ollama container
(`:11434`) serving Qwen. To record against real OpenAI instead, set
`OPENAI_API_KEY` and point the `codex-probe` service's command at
`examples/openai_backend.json`; no rebuild needed, since backend
selection is config-only. See [`Dockerfile`](Dockerfile) and
[`compose.yaml`](compose.yaml).

*(The Docker setup was authored and reviewed but not build-verified in
the environment this project was developed in, since Docker was not
available there; see [Limitations](#limitations).)*

## Tests

```bash
pip install -e ".[dev]"
pytest -q --cov=codex_probe --cov-report=term-missing
ruff check .
mypy src/codex_probe
```

67 tests across config validation, header handling, non-streaming
passthrough, streaming passthrough, the log schema and session store, and
the full `ProxyRecorder` lifecycle against a real backend over a real
socket. CI (`.github/workflows/tests.yml`) runs the same on every push
across Python 3.11 and 3.12.

## Experimental results

Methodology, what was actually verified in this environment vs. what
requires a live Codex run with real credentials/hardware, and a template
for recording an OpenAI-vs-Qwen comparison:
[`docs/experiment-results.md`](docs/experiment-results.md).

## Security

- API keys are read from environment variables named in config
  (`backend.api_key_env`), never written into config files.
- `authorization`, `api-key`, `x-api-key`, and similar header values are
  redacted (`Bearer [REDACTED]`) before being written to any log file.
- `.env`, `logs/`, and other local/generated artifacts are excluded from
  version control (see [`.gitignore`](.gitignore)).
- CodexProbe is a local development/research tool: it binds to
  `127.0.0.1` by default and has no authentication of its own. Do not
  expose it on a shared or public network without adding a layer in
  front of it.

## Limitations

- No translation between OpenAI's Responses API and Chat Completions:
  Codex and the configured backend must agree on `wire_api` (see
  [Responses API vs Chat Completions](#responses-api-vs-chat-completions)).
- Log writes are synchronous file I/O on the request-handling event loop;
  fine for the call volumes and log sizes an interactive Codex session
  produces, but not designed for extremely high request throughput.
- The live OpenAI-vs-Qwen experimental comparison requires a real
  OpenAI API key and either a local Ollama/vLLM install or a GPU
  machine; both unavailable in the environment this project was built
  in, so `docs/experiment-results.md` documents methodology and a
  results template rather than fabricated numbers.
- The Docker setup was written and reviewed but not build-verified
  (Docker was unavailable in that environment).
- CodexProbe assumes Codex's own `base_url` path (e.g. `/v1`) matches
  the backend's; see the path-handling note in
  [`src/codex_probe/transport.py`](src/codex_probe/transport.py) for
  exactly how the two are reconciled.

## Project structure

```
codex-llm-call-recorder/
├── .github/workflows/tests.yml   # CI: pytest + ruff + mypy on 3.11/3.12
├── docs/                         # architecture, config, log format, backend swap, results
├── examples/                     # runnable script + backend configs + Codex config
├── src/codex_probe/
│   ├── __init__.py               # public API: ProxyRecorder
│   ├── config.py                 # raw dict -> validated RecorderConfig
│   ├── models.py                 # CapturedCall schema
│   ├── errors.py                 # ConfigError, UpstreamError
│   ├── headers.py                # forwarding, auth injection, redaction
│   ├── transport.py              # persistent httpx.AsyncClient to one backend
│   ├── streaming.py              # live SSE forward + capture
│   ├── proxy.py                  # FastAPI catch-all reverse proxy
│   ├── logging_store.py          # per-session calls.jsonl + metadata.json
│   ├── recorder.py                # ProxyRecorder: start()/stop() lifecycle
│   └── cli.py                    # `codex-probe --config ...`
├── tests/                        # 67 tests across every module above
├── Dockerfile / compose.yaml     # reproducible runtime + Ollama topology
└── pyproject.toml
```

## License

MIT. See [`LICENSE`](LICENSE).
