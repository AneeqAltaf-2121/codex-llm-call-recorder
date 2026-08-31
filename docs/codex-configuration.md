# Configuring Codex CLI to use CodexProbe

CodexProbe never modifies Codex. Codex is simply told, through its own
`~/.codex/config.toml`, to use CodexProbe as a `model_provider` --
exactly the same mechanism you would use to point Codex at any other
OpenAI-compatible server.

## 1. Start CodexProbe first

Codex needs somewhere to connect to before it can be pointed at it:

```bash
codex-probe --config examples/openai_backend.json
```

This prints the local endpoint (e.g. `http://127.0.0.1:8135/v1`) and
keeps running, recording every call, until you press Ctrl+C.

## 2. Point Codex at that endpoint

Add a `[model_providers.codex_probe]` section to `~/.codex/config.toml`
(a full example lives at `examples/codex-config-example.toml`):

```toml
[model_providers.codex_probe]
name = "CodexProbe"
base_url = "http://127.0.0.1:8135/v1"
wire_api = "responses"
env_key = "OPENAI_API_KEY"

model_provider = "codex_probe"
model = "gpt-5-codex"
```

| Field            | Meaning                                                                                          |
| ---------------- | ------------------------------------------------------------------------------------------------- |
| `base_url`       | Must match the endpoint CodexProbe printed on startup. This is the entire integration point.      |
| `wire_api`       | The OpenAI-compatible protocol Codex will use to talk to `base_url`. **Must match** the `wire_api` in CodexProbe's own backend config -- CodexProbe does not translate between protocols (see `docs/architecture.md`). |
| `env_key`        | Name of an environment variable Codex reads an API key from, to send in its own `Authorization` header. Can be a placeholder for local/unauthenticated backends -- CodexProbe injects the *real* backend credential independently, from its own config's `backend.api_key_env` (see `docs/backend-swap.md`). |
| `model_provider` | Tells Codex to actually use the provider defined above, rather than its default.                  |
| `model`          | Whatever model name the real backend expects. Must be a model the *backend* CodexProbe is currently configured for actually serves. |

## 3. Run Codex normally

```bash
codex exec "Create a Python function that reverses a string and add a unit test."
```

Codex behaves exactly as it would talking to the backend directly --
CodexProbe adds no visible latency or behavior change (see
`docs/architecture.md`). Every request it made and every response it got
back, including full tool schemas and streamed output, is now on disk
under `logs/<session-id>/` (see `docs/log-format.md`).

## 4. Stop CodexProbe

Ctrl+C in the terminal running `codex-probe`. It finishes writing
`metadata.json` and prints how many calls were recorded.

## Switching backends

Only the `wire_api` (if it differs) and CodexProbe's own `--config` file
need to change -- Codex's `base_url` stays `http://127.0.0.1:8135/v1`
either way. See `docs/backend-swap.md`.
