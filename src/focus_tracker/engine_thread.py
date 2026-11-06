"""EngineThread: the engine's asyncio loop on a daemon thread, controlled by the Qt window.

Hosts the tick loop and the extension WebSocket (ws://127.0.0.1:8765/ws/browser). Implements
`schema.EngineHandle`.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
from collections.abc import Callable
from typing import Any

import uvicorn

from focus_tracker.main import Runtime, build_runtime
from focus_tracker.schema import UiState, VisionLike
from focus_tracker.server import HOST, PORT, create_app

log = logging.getLogger("focus_tracker")

STOP_TIMEOUT_S = 10.0


class EngineThread:
    """Runs `Runtime` plus the extension server on a background thread."""

    def __init__(
        self,
        runtime_factory: Callable[[], Runtime] = build_runtime,
        *,
        port: int = PORT,
        serve: bool = True,
    ) -> None:
        self._factory = runtime_factory
        self._port = port
        self._serve = serve
        self._runtime: Runtime | None = None
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_event: asyncio.Event | None = None
        self._ready = threading.Event()

    # ------------------------------------------------------------ EngineHandle

    def start(self, on_update: Callable[[UiState], None]) -> None:
        """Build the runtime, start the thread, return once the loop is accepting commands.
        `on_update` is called from the engine thread."""
        if self._thread is not None:
            return
        self._runtime = self._factory()
        self._thread = threading.Thread(target=self._run, args=(on_update,), name="engine", daemon=True)
        self._thread.start()
        self._ready.wait(STOP_TIMEOUT_S)

    def command(self, msg: dict[str, Any]) -> None:
        """Thread-safe; the resulting state arrives through `on_update`."""
        runtime, loop = self._runtime, self._loop
        if runtime is None or loop is None or loop.is_closed():
            return
        asyncio.run_coroutine_threadsafe(self._command(runtime, msg), loop)

    def vision(self) -> VisionLike | None:
        return self._runtime.vision if self._runtime else None

    def stop(self) -> None:
        """Stop the server, release the camera and join the thread."""
        loop, event, thread = self._loop, self._stop_event, self._thread
        if thread is None:
            return
        if loop is not None and event is not None and not loop.is_closed():
            with contextlib.suppress(RuntimeError):  # loop closed between the check and the call
                loop.call_soon_threadsafe(event.set)
        thread.join(STOP_TIMEOUT_S)
        self._thread = None

    # ------------------------------------------------------------ thread body

    @staticmethod
    async def _command(runtime: Runtime, msg: dict[str, Any]) -> None:
        try:
            await runtime.handle_command(msg)
        except Exception:
            log.exception("command failed: %r", msg.get("cmd"))

    def _run(self, on_update: Callable[[UiState], None]) -> None:
        try:
            asyncio.run(self._main(on_update))
        except Exception:
            log.exception("engine thread crashed")
        finally:
            self._ready.set()

    async def _main(self, on_update: Callable[[UiState], None]) -> None:
        runtime = self._runtime
        assert runtime is not None
        self._loop = asyncio.get_running_loop()
        self._stop_event = asyncio.Event()
        runtime.start(on_update)
        tasks = [asyncio.create_task(runtime.run())]
        server: uvicorn.Server | None = None
        if self._serve:
            app = create_app(runtime.on_browser_event, runtime.on_browser_connection)
            server = uvicorn.Server(uvicorn.Config(app, host=HOST, port=self._port, log_level="warning", lifespan="off"))
            tasks.append(asyncio.create_task(self._serve_extension(server)))
        self._ready.set()
        try:
            await self._stop_event.wait()
        finally:
            if server is not None:
                server.should_exit = True
            tasks[0].cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            runtime.stop()

    @staticmethod
    async def _serve_extension(server: uvicorn.Server) -> None:
        """Run uvicorn; a busy port only disables the extension link, the app keeps working."""
        try:
            await server.serve()
        except SystemExit:  # uvicorn exits the process when it cannot bind
            log.error("extension server could not start (port in use?); browser sensing is off")
