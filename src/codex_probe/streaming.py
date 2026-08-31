"""Streaming (SSE) passthrough: forward backend chunks live, capture in full.

Codex commonly receives Server-Sent Events from the Responses API. The
property this module exists to guarantee is that CodexProbe never
buffers a streamed response before relaying it -- doing that would
defeat the entire purpose of streaming, since Codex would see nothing
until the backend had finished responding. Instead, every chunk is
yielded to the client the instant it arrives *and* appended to an
in-memory buffer at the same time:

    async for chunk in upstream.aiter_bytes():
        buffer.append(chunk)
        yield chunk

Once the stream ends (or is interrupted), the buffered chunks are
reassembled into the complete response body and handed to a callback for
logging -- so the log always contains the full response even though
Codex consumed it incrementally.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable

import httpx

#: Invoked exactly once, after the stream ends, with the fully
#: reassembled body, the response's status code, and its headers.
OnStreamComplete = Callable[[bytes, int, dict[str, str]], Awaitable[None]]


def looks_like_event_stream(content_type: str) -> bool:
    """Whether a response ``Content-Type`` indicates Server-Sent Events."""
    return "text/event-stream" in content_type.lower()


async def stream_and_capture(
    upstream_response: httpx.Response,
    *,
    on_complete: OnStreamComplete,
) -> AsyncIterator[bytes]:
    """Yield each chunk of ``upstream_response`` to the caller as it arrives.

    Every chunk is forwarded immediately -- never held back to be
    batched -- and simultaneously appended to an internal buffer. When
    the stream is exhausted, or if iteration is interrupted (the client
    disconnects, an exception propagates), the upstream response is
    closed and ``on_complete`` is awaited exactly once with everything
    captured so far. This guarantees a log record is produced even for a
    stream that ends abnormally.
    """
    chunks: list[bytes] = []
    status_code = upstream_response.status_code
    headers = dict(upstream_response.headers)
    try:
        async for chunk in upstream_response.aiter_bytes():
            if not chunk:
                continue
            chunks.append(chunk)
            yield chunk
    finally:
        await upstream_response.aclose()
        await on_complete(b"".join(chunks), status_code, headers)
