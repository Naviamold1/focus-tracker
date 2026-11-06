"""FastAPI app with the extension WebSocket only (127.0.0.1). The UI is a local Qt window."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from focus_tracker.schema import SCHEMA_VERSION, BrowserEvent

log = logging.getLogger(__name__)

HOST = "127.0.0.1"
PORT = 8765
_EXTENSION_SCHEMES = ("chrome-extension://", "moz-extension://")


_warned_version = False


def parse_browser_event(raw: object) -> BrowserEvent | None:
    """Validate one extension frame. Unknown `v` is rejected with a single warning."""
    global _warned_version
    if not isinstance(raw, dict):
        return None
    if raw.get("v") != SCHEMA_VERSION:
        if not _warned_version:
            _warned_version = True
            log.warning("rejecting browser event with unsupported v=%r", raw.get("v"))
        return None

    def opt_str(key: str) -> str | None:
        value = raw.get(key)
        return value if isinstance(value, str) and value else None

    ts = raw.get("ts")
    return BrowserEvent(
        url=opt_str("url"),
        domain=opt_str("domain"),
        page_title=opt_str("pageTitle"),
        audible=raw.get("audible") is True,
        incognito=raw.get("incognito") is True,
        ts=ts if isinstance(ts, int) else 0,
    )


def _origin_ok(ws: WebSocket) -> bool:
    """Only the extension (or a non-browser client without Origin) may connect: blocks web pages."""
    origin = ws.headers.get("origin")
    return origin is None or origin.startswith(_EXTENSION_SCHEMES)


def create_app(
    on_event: Callable[[BrowserEvent], None],
    on_connection: Callable[[bool], None] = lambda _: None,
) -> FastAPI:
    """Build the app. `on_event` gets each valid frame; `on_connection(True/False)` tracks the socket."""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.websocket("/ws/browser")
    async def ws_browser(ws: WebSocket) -> None:
        if not _origin_ok(ws):
            await ws.close(code=1008)
            return
        await ws.accept()
        on_connection(True)
        try:
            while True:
                try:
                    raw = json.loads(await ws.receive_text())
                except ValueError:
                    continue
                event = parse_browser_event(raw)
                if event is not None:
                    on_event(event)
        except WebSocketDisconnect:
            pass
        finally:
            on_connection(False)

    return app
