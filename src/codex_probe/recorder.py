"""``ProxyRecorder``: the public interface required by the assignment.

    from codex_probe import ProxyRecorder

    recorder = ProxyRecorder(config)
    endpoint = recorder.start()

    # ... point Codex's model_provider.base_url at `endpoint` and run it ...

    calls = recorder.stop()

``ProxyRecorder`` owns the entire session lifecycle: validating
configuration, creating a session, starting a background Uvicorn server
that hosts the reverse proxy, and tearing everything down cleanly on
``stop()`` while returning every call captured during the session, in
order.

The server runs on a dedicated background thread with its own asyncio
event loop. That is what lets ``start()``/``stop()`` be plain synchronous
methods: the caller's script (which drives Codex as a separate OS
process) never has to run an event loop itself.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

import uvicorn

from codex_probe.config import RecorderConfig, load_config
from codex_probe.logging_store import SessionLogStore, new_session_id
from codex_probe.models import CapturedCall
from codex_probe.proxy import create_app
from codex_probe.transport import Transport

_STARTUP_POLL_INTERVAL = 0.01
_STARTUP_TIMEOUT = 10.0
_SHUTDOWN_TIMEOUT = 10.0


class ProxyRecorder:
    """Records every LLM call Codex makes through a local reverse proxy."""

    def __init__(self, config: dict[str, Any]) -> None:
        """Validate ``config`` immediately (fail fast, before ``start()``)."""
        self.config: RecorderConfig = load_config(config)
        self.session_id: str | None = None

        self._transport: Transport | None = None
        self._store: SessionLogStore | None = None
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self._actual_port: int | None = None
        self._stopped = False

    @property
    def endpoint(self) -> str | None:
        """The local proxy URL Codex should be pointed at, once started."""
        if self._actual_port is None:
            return None
        return f"http://{self.config.listen_host}:{self._actual_port}/v1"

    def start(self) -> str:
        """Start a new recording session and return the local endpoint URL.

        The returned URL (e.g. ``http://127.0.0.1:8135/v1``) is what
        Codex's ``model_provider.base_url`` should be configured to,
        exactly as it would be configured against a real OpenAI-compatible
        backend -- CodexProbe is meant to be a drop-in substitute.
        """
        if self._server is not None:
            raise RuntimeError("ProxyRecorder.start() was already called on this instance")

        self.session_id = new_session_id()
        self._store = SessionLogStore(
            self.config.log_dir,
            self.session_id,
            backend_name=self.config.backend.name,
            wire_api=self.config.backend.wire_api,
        )
        self._transport = Transport(self.config.backend, timeout=self.config.request_timeout)

        async def on_call(call: CapturedCall) -> None:
            self._store.record_call(call)

        app = create_app(
            config=self.config,
            transport=self._transport,
            session_id=self.session_id,
            on_call=on_call,
        )

        uvicorn_config = uvicorn.Config(
            app,
            host=self.config.listen_host,
            port=self.config.listen_port,
            log_level="warning",
        )
        self._server = uvicorn.Server(uvicorn_config)

        ready = threading.Event()
        self._thread = threading.Thread(
            target=self._run_server, args=(ready,), daemon=True, name="codex-probe-server"
        )
        self._thread.start()

        if not ready.wait(timeout=_STARTUP_TIMEOUT):
            raise RuntimeError("CodexProbe proxy failed to start within the startup timeout")

        self._actual_port = self._resolve_actual_port()
        return self.endpoint  # type: ignore[return-value]

    def stop(self) -> list[dict]:
        """Stop the session: close the proxy, flush logs, return all calls.

        Returns every call captured during this session as plain dicts,
        in the order they occurred. Safe to call only once per ``start()``.
        """
        if self._server is None:
            raise RuntimeError("ProxyRecorder.stop() called before start()")
        if self._stopped:
            raise RuntimeError("ProxyRecorder.stop() was already called on this instance")

        self._server.should_exit = True
        self._thread.join(timeout=_SHUTDOWN_TIMEOUT)
        self._stopped = True

        return self._store.close()

    def _run_server(self, ready: threading.Event) -> None:
        """Thread entry point: run the proxy's own event loop to completion.

        The transport's HTTP client is closed here too, on the same
        event loop it made requests on, right after the server finishes
        shutting down -- avoiding any cross-thread/cross-loop asyncio
        object usage.
        """
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def lifespan() -> None:
            server_task = asyncio.ensure_future(self._server.serve())
            while not self._server.started and not server_task.done():
                await asyncio.sleep(_STARTUP_POLL_INTERVAL)
            ready.set()
            await server_task
            await self._transport.aclose()

        try:
            loop.run_until_complete(lifespan())
        finally:
            loop.close()

    def _resolve_actual_port(self) -> int:
        if self.config.listen_port != 0:
            return self.config.listen_port
        # listen_port == 0 means "let the OS pick a free port" -- read
        # back whatever it actually bound to.
        sockets = self._server.servers[0].sockets
        return sockets[0].getsockname()[1]
