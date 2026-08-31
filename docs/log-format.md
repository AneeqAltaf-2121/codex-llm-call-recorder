# Log format

Each recorder session gets its own directory under `log_dir`:

```
logs/
└── 20260830_204503_a6b31d/
    ├── metadata.json
    └── calls.jsonl
```

The session id (`20260830_204503_a6b31d`) is timestamp-prefixed so
sessions sort chronologically on disk, and suffixed with a short random
token so two sessions starting in the same second never collide (see
`codex_probe/logging_store.py`).

## `metadata.json`

Session-level facts that don't belong on every individual call:

```json
{
  "session_id": "20260830_204503_a6b31d",
  "started_at": "2026-08-30T20:45:03.120Z",
  "ended_at": "2026-08-30T20:47:11.884Z",
  "backend": "qwen-ollama",
  "wire_api": "chat_completions"
}
```

`ended_at` is `null` until the session is stopped -- a `metadata.json`
with `ended_at: null` on disk means CodexProbe is still running (or was
killed without a clean shutdown).

## `calls.jsonl`

One JSON object per line, one per call, in the order calls occurred --
so a researcher (or a script) can replay a session call by call: prompt
1, response 1, tool action, prompt 2, response 2, and so on. Each line
matches this schema (`codex_probe/models.py:CapturedCall`):

```json
{
  "call_id": "8f14e45f-ceea-4c9d-8f43-1b4b3a2b6b1a",
  "sequence": 1,
  "session_id": "20260830_204503_a6b31d",
  "timestamp": "2026-08-30T20:45:04.001Z",

  "backend": {
    "name": "openai",
    "base_url": "https://api.openai.com/v1",
    "wire_api": "responses"
  },

  "request": {
    "method": "POST",
    "path": "/v1/responses",
    "headers": {
      "content-type": "application/json",
      "authorization": "Bearer [REDACTED]"
    },
    "body": {
      "model": "gpt-5-codex",
      "instructions": "You are Codex, a coding agent...",
      "input": [
        { "role": "user", "content": "Create a Python function that reverses a string." }
      ],
      "tools": [{ "type": "shell", "name": "shell" }]
    }
  },

  "response": {
    "status_code": 200,
    "headers": { "content-type": "text/event-stream" },
    "body": "data: {\"type\": \"response.output_text.delta\", \"delta\": \"def \"}\n\n...(full reassembled stream)..."
  },

  "streaming": true,
  "latency_ms": 824.3
}
```

| Field                | Meaning                                                                                 |
| --------------------- | ------------------------------------------------------------------------------------------ |
| `call_id`             | A random UUID, unique per call.                                                          |
| `sequence`            | 1-based position of this call within its session, in call order.                          |
| `session_id`          | Matches the session directory name and `metadata.json`.                                   |
| `timestamp`           | ISO-8601 UTC, when the call started.                                                       |
| `backend.*`           | Which backend this specific call went to (mirrors the session's `metadata.json`).          |
| `request.method`      | HTTP method Codex used (almost always `POST`, but `GET /v1/models` also passes through).   |
| `request.path`        | Full path Codex requested, e.g. `/v1/responses` or `/v1/chat/completions`.                 |
| `request.headers`     | Every header Codex sent, with sensitive values (`authorization`, `api-key`, etc.) redacted -- see `codex_probe/headers.py`. |
| `request.body`        | The **complete** request body: full system/developer instructions, full conversation and tool-call history, full tool schema. Parsed JSON where the body is JSON (the normal case), otherwise decoded text. Never truncated, never a summary. |
| `response.status_code`| HTTP status the backend returned.                                                          |
| `response.headers`    | Response headers, redacted the same way as request headers.                                |
| `response.body`       | The **complete** response. For a non-streaming call, the parsed JSON body. For a streaming call, the fully reassembled text of every SSE chunk, in order -- exactly what `stream_and_capture` accumulated while forwarding it live (see `docs/architecture.md`). |
| `streaming`           | Whether this call was served as a live SSE stream rather than a single buffered body.      |
| `latency_ms`          | Wall-clock time from when CodexProbe sent the request upstream to when the response (or, for streaming, the *first* byte triggering the response) was ready. |

## Redaction

Header values that could be credentials (`authorization`, `api-key`,
`x-api-key`, `openai-api-key`, `proxy-authorization`) are redacted before
being written to disk (a `Bearer ...` value becomes `Bearer [REDACTED]`)
so session logs can be shared or committed to a research write-up without
leaking API keys. This redaction only affects the on-disk record --
CodexProbe still sees and forwards the real header value to the backend.

Bodies are never redacted: the assignment's core requirement is a
faithful, complete record of every prompt and response, and secrets do
not normally appear inside a request/response body in OpenAI-compatible
traffic.
