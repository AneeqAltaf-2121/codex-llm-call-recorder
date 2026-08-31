# Backend swap: OpenAI vs. local Qwen

This is the core research capability of the project: the exact same
CodexProbe code (`src/codex_probe/`) records against a hosted OpenAI
backend or against a locally hosted Qwen model behind Ollama/vLLM, and
the only thing that differs between the two runs is a config file.

## Config A: OpenAI (`examples/openai_backend.json`)

```json
{
  "listen_host": "127.0.0.1",
  "listen_port": 8135,
  "backend": {
    "name": "openai",
    "base_url": "https://api.openai.com/v1",
    "wire_api": "responses",
    "api_key_env": "OPENAI_API_KEY"
  },
  "log_dir": "./logs",
  "seed": 123
}
```

## Config B: local Qwen via Ollama (`examples/qwen_ollama_backend.json`)

```json
{
  "listen_host": "127.0.0.1",
  "listen_port": 8135,
  "backend": {
    "name": "qwen-ollama",
    "base_url": "http://127.0.0.1:11434/v1",
    "wire_api": "chat_completions"
  },
  "log_dir": "./logs",
  "seed": 123
}
```

Notice what changed: `backend.name`, `backend.base_url`,
`backend.wire_api`, and the absence of `api_key_env` (Ollama's OpenAI-
compatible server needs no authentication). **`src/codex_probe/` did not
change at all.** `codex_probe.config.load_config` validates whichever of
these you pass to `ProxyRecorder`, and every other module
(`transport.py`, `proxy.py`, `streaming.py`, `logging_store.py`) is
already backend-agnostic: it only ever reads `config.backend.*`, never
a hardcoded provider name.

## Why `wire_api` still matters here

Codex's *own* config (`~/.codex/config.toml`) also has a `wire_api`
field, and Codex uses it to decide what shape of request to build
(`/responses` vs. `/chat/completions`). CodexProbe does not translate
between the two: whatever protocol Codex was told to speak is what
arrives at CodexProbe's catch-all route, and CodexProbe forwards it
byte-for-byte. So when you swap CodexProbe from Config A to Config B,
**Codex's own config must also change its `wire_api`** to match the new
backend (`responses` for OpenAI; `chat_completions` for Ollama/vLLM),
otherwise the backend will receive a request shaped for the wrong
protocol and most likely return an error. That mismatch, if you hit it,
is itself a real, recordable data point (see
`docs/experiment-results.md`).

## Setting up a local Qwen backend

### Option 1: Ollama (simpler; recommended on a laptop)

```bash
# Install Ollama: https://ollama.com/download
ollama pull qwen2.5-coder:7b
ollama serve   # usually already running as a background service
```

Ollama exposes an OpenAI-compatible server at `http://127.0.0.1:11434/v1`
by default, exactly what `examples/qwen_ollama_backend.json` points at.
Approximate requirements: ~8 GB of free disk for the 7B quantized model,
and enough RAM to hold it (16 GB system RAM is a comfortable minimum for
the 7B variant on CPU; a GPU with ~8 GB VRAM makes it noticeably faster).

### Option 2: vLLM (a more research-server-like environment; needs a GPU)

```bash
pip install vllm
python -m vllm.entrypoints.openai.api_server \
    --model Qwen/Qwen2.5-Coder-7B-Instruct \
    --port 8000
```

vLLM exposes an OpenAI-compatible server at `http://127.0.0.1:8000/v1`.
See `examples/qwen_vllm_backend.json`. This needs a CUDA-capable GPU
with enough VRAM for the chosen model (roughly 16 GB+ for an unquantized
7B model); it is not a realistic CPU-only option.

### Docker Compose

`compose.yaml` wires a `codex-probe` container to an `ollama` container
on the same Docker network. Because containers address each other by
service name rather than `127.0.0.1`, the compose setup uses
`examples/qwen_ollama_backend.docker.json` (`base_url:
http://ollama:11434/v1`) rather than the plain
`examples/qwen_ollama_backend.json` used outside Docker. See the
Dockerfile and compose.yaml for exact commands.

## Running the swap

```bash
# Session 1: against OpenAI
codex-probe --config examples/openai_backend.json
# (point Codex's wire_api = "responses", run a task, Ctrl+C)

# Session 2: against local Qwen
codex-probe --config examples/qwen_ollama_backend.json
# (point Codex's wire_api = "chat_completions", run the same task, Ctrl+C)
```

Then inspect `logs/<session-2-id>/metadata.json` and confirm
`"backend": "qwen-ollama"`, proof the swap took effect purely through
configuration. See `docs/experiment-results.md` for how to compare the
two sessions.

## If Qwen's tool calling fails

Not every locally hosted model implements OpenAI-style tool/function
calling as completely or reliably as OpenAI's own models. If Codex's
tool calls come back malformed, get ignored, or cause the model to loop,
that is itself a legitimate, useful experimental finding; record what
happened in `docs/experiment-results.md` rather than treating it as a
CodexProbe bug (check `logs/<session>/calls.jsonl` first to see exactly
what CodexProbe sent and what the backend returned).
