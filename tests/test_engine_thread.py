"""EngineThread lifecycle against fakes (no camera, network or real watcher), plus the WS endpoint."""

from __future__ import annotations

import queue
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from focus_tracker.config import ConfigStore
from focus_tracker.engine_thread import EngineThread
from focus_tracker.main import Runtime
from focus_tracker.schema import (
    AppSignal,
    BrowserEvent,
    Classification,
    Surface,
    UiState,
)
from focus_tracker.server import create_app


class FakeWatcher:
    started = stopped = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def latest(self) -> AppSignal | None:
        return AppSignal(app="Visual Studio Code", process="Code.exe", title="spec.md", input_idle_sec=1.0, ts=0)


class FakeClassifier:
    closed = False

    def classify(self, goal: str, surface: Surface) -> Classification:
        return Classification(key="k", category="productive", source="jev", confidence=0.9, pending=False)

    def on_goal_changed(self, goal: str) -> None: ...
    def cache_entries(self) -> list[dict[str, object]]:
        return []

    def close(self) -> None:
        self.closed = True


def next_state(q: queue.Queue[UiState], pred=lambda s: True, timeout: float = 5.0) -> UiState:
    """Wait for the first queued state satisfying `pred`."""
    while True:
        state = q.get(timeout=timeout)
        if pred(state):
            return state


def test_engine_thread_updates_commands_and_stops(tmp_path: Path) -> None:
    watcher, classifier = FakeWatcher(), FakeClassifier()
    runtime = Runtime(ConfigStore(tmp_path), classifier, None, watcher=watcher, hotkeys=False)
    engine = EngineThread(lambda: runtime, serve=False)
    states: queue.Queue[UiState] = queue.Queue()
    engine.start(states.put)
    try:
        first = next_state(states)
        assert first.app is not None and first.app.process == "Code.exe"
        assert first.jev_online and not first.browser_connected
        assert engine.vision() is None

        engine.command({"cmd": "set_goal", "goal": "write the spec"})
        after = next_state(states, lambda s: s.tick.goal == "write the spec")
        assert after.goal_history[0] == "write the spec"
    finally:
        engine.stop()
    assert watcher.stopped and classifier.closed


def test_browser_socket_feeds_runtime(tmp_path: Path) -> None:
    runtime = Runtime(ConfigStore(tmp_path), None, None, watcher=FakeWatcher(), hotkeys=False)
    client = TestClient(create_app(runtime.on_browser_event, runtime.on_browser_connection))
    frame = {"v": 1, "url": "youtube.com/watch", "domain": "youtube.com", "pageTitle": "cats", "ts": 5}
    with client.websocket_connect("/ws/browser") as ws:
        ws.send_json({**frame, "v": 99})  # wrong version: dropped
        ws.send_json(frame)
        ws.send_json({**frame, "pageTitle": "second"})  # processed in order, so the first is done by now
        assert runtime._browser_sockets == 1
    assert runtime._browser_sockets == 0
    assert isinstance(runtime._browser, BrowserEvent) and runtime._browser.domain == "youtube.com"


def test_browser_socket_rejects_web_page_origin(tmp_path: Path) -> None:
    runtime = Runtime(ConfigStore(tmp_path), None, None, watcher=FakeWatcher(), hotkeys=False)
    client = TestClient(create_app(runtime.on_browser_event))
    with pytest.raises(WebSocketDisconnect), client.websocket_connect(
        "/ws/browser", headers={"origin": "https://evil.example"}
    ):
        pass
