"""Wires watcher, fusion, classifier, vision and decision engine into one runtime.

`Runtime` is pure wiring on an asyncio loop; `engine_thread.EngineThread` hosts it for the Qt app
and `run_headless()` hosts it with a logging sink.
"""

from __future__ import annotations

import asyncio
import logging
import logging.handlers
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from focus_tracker import demo
from focus_tracker.config import ConfigStore, data_dir, load_env
from focus_tracker.decision import DecisionEngine, Effects, EngineInputs, Settings
from focus_tracker.fusion import SurfaceDebouncer, fuse
from focus_tracker.schema import (
    AppSignal,
    AppWatcherLike,
    BrowserEvent,
    Classification,
    Classifier,
    Clock,
    Surface,
    Tick,
    UiState,
    VisionLike,
)
from focus_tracker.toast import show_toast
from focus_tracker.watcher import AppWatcher

log = logging.getLogger("focus_tracker")

TICK_SEC = 2.0
CATEGORIES = ("productive", "neutral", "distraction")
G_SURFACE_HOLD_MS = 5 * 60 * 1000
CACHE_ROWS = 40
# UI-editable thresholds: name -> (lower, upper). Engine settings except yaw_max.
THRESHOLD_BOUNDS = {
    "dwell_sec": (30.0, 120.0),
    "afk_sec": (60.0, 600.0),
    "trust_conf": (0.5, 1.0),
    "yaw_max": (10.0, 35.0),
}

UpdateSink = Callable[[UiState], None]


class Runtime:
    """Owns the live pipeline. Every method runs on the engine's asyncio loop thread."""

    def __init__(
        self,
        cfg: ConfigStore,
        classifier: Classifier | None,
        vision: VisionLike | None,
        clock: Clock | None = None,
        watcher: AppWatcherLike | None = None,
        *,
        hotkeys: bool = True,
    ) -> None:
        self.cfg = cfg
        self.classifier = classifier
        self.vision = vision
        self.clock = clock or Clock()
        self.watcher: AppWatcherLike = watcher or AppWatcher()
        self.debouncer = SurfaceDebouncer()
        self.engine = DecisionEngine(Settings.from_dict(cfg.data["thresholds"]))
        self.engine.overrides.update(cfg.data["overrides"])
        self.engine.snooze_until_ms = int(cfg.data["snooze_until_ms"])
        self.demo = demo.DemoRunner()
        self._hotkeys = hotkeys
        self._browser: BrowserEvent | None = None
        self._browser_sockets = 0
        self._browser_last_ms: int | None = None
        self._synthetic: tuple[Surface, int] | None = None  # (surface, expires_ms) from the G scenario
        self._last_surface: Surface | None = None
        self._last_app: AppSignal | None = None
        self._last_key: str | None = None
        self._last_classification: Classification | None = None
        self._on_update: UpdateSink | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    # ------------------------------------------------------------ lifecycle

    def start(self, on_update: UpdateSink) -> None:
        """Start sensors. Must be called on the loop that will run `run()`."""
        self._on_update = on_update
        self._loop = asyncio.get_running_loop()
        self.watcher.start()
        if self.vision is not None:
            self.vision.start()
            self.vision.set_enabled(self.cfg.data["camera_on"])
        set_override = getattr(self.classifier, "set_override", None)
        if set_override is not None:
            for key, category in self.cfg.data["overrides"].items():
                set_override(key, category)
        if self._hotkeys:
            demo.register_hotkeys(self._hotkey)

    def stop(self) -> None:
        """Release the camera, watcher, classifier and hotkeys."""
        self.demo.stop()
        if self._hotkeys:
            demo.unregister_hotkeys()
        self.watcher.stop()
        if self.vision is not None:
            self.vision.stop()
        close = getattr(self.classifier, "close", None)
        if close is not None:
            close()

    def _hotkey(self, letter: str) -> None:
        """Called from the keyboard hook thread."""
        if self._loop is not None:
            asyncio.run_coroutine_threadsafe(self.handle_command({"cmd": "demo", "which": letter}), self._loop)

    # ------------------------------------------------------------ inputs

    def on_browser_event(self, event: BrowserEvent) -> None:
        """Stamp with local receive time so recency does not depend on the extension's clock."""
        now = self.clock.now_ms()
        self._browser = replace(event, ts=now)
        self._browser_last_ms = now

    def on_browser_connection(self, connected: bool) -> None:
        """Extension socket opened or closed (several windows may connect at once)."""
        self._browser_sockets = max(0, self._browser_sockets + (1 if connected else -1))

    # ------------------------------------------------------------ tick loop

    async def run(self) -> None:
        """2 s live tick loop; idles while the D demo owns the display."""
        loop = asyncio.get_running_loop()
        while True:
            started = loop.time()
            if not self.demo.active:
                await self._safe_live_tick()
            await asyncio.sleep(max(0.05, TICK_SEC - (loop.time() - started)))

    async def _safe_live_tick(self) -> None:
        try:
            await self._live_tick()
        except Exception:
            log.exception("tick failed")

    async def _live_tick(self) -> None:
        now = self.clock.now_ms()
        app = self.watcher.latest()
        if self._synthetic is not None and now < self._synthetic[1]:
            surface: Surface | None = self._synthetic[0]
        else:
            self._synthetic = None
            surface = self.debouncer.update(fuse(app, self._browser, now), now)
        self._last_surface = surface
        self._last_app = app
        goal: str = self.cfg.data["goal"]
        classification = None
        if goal and surface is not None and self.classifier is not None:
            try:
                classification = self.classifier.classify(goal, surface)
            except Exception:
                log.exception("classifier failed")
        self._last_key = classification.key if classification else None
        self._last_classification = classification
        inputs = EngineInputs(
            goal=goal,
            surface=surface,
            classification=classification,
            vision=self.vision.latest() if self.vision else None,
            camera_on=self.cfg.data["camera_on"],
            input_idle_sec=app.input_idle_sec if app else 0.0,
        )
        tick, effects = self.engine.step(inputs, now)
        self._apply_effects(effects)
        extra = {
            **self.engine.snapshot(),
            "input_idle_sec": inputs.input_idle_sec,
            "facing": inputs.vision.facing if inputs.vision else None,
            "baseline": self._baseline_text(),
        }
        self._publish(tick, extra, demo_active=False)

    def _baseline_text(self) -> str:
        """Calibration readout for the camera panel, e.g. 'yaw -3° pitch +8°'."""
        baseline = getattr(self.vision, "baseline", None)
        if baseline is None:
            return "default"
        b = baseline()
        if b.yaw == 0 and b.pitch == 0:
            return "not calibrated"
        return f"yaw {b.yaw:+.0f}° pitch {b.pitch:+.0f}°"

    def _apply_effects(self, effects: Effects) -> None:
        """Run side effects: escalation toast and afk camera release/restore."""
        if effects.toast is not None:
            show_toast(*effects.toast)
        if effects.camera_release is not None and self.vision is not None:
            self.vision.set_enabled(not effects.camera_release and self.cfg.data["camera_on"])

    def _jev_status(self) -> tuple[bool, float | None]:
        """(online, last round-trip ms). Uses classifier attributes when it exposes them."""
        if self.classifier is None:
            return False, None
        online = getattr(self.classifier, "jev_online", None)
        if online is None:
            last = self._last_classification
            online = not (last is not None and last.remote_unreachable)
        return bool(online), getattr(self.classifier, "jev_last_ms", None)

    def _publish(self, tick: Tick, extra: dict[str, Any], *, demo_active: bool) -> None:
        """Build the UiState for this tick and hand it to the sink."""
        if self._on_update is None:
            return
        entries = getattr(self.classifier, "cache_entries", None)
        cache = sorted(entries() if entries is not None else [], key=lambda e: e.get("ts") or 0, reverse=True)
        jev_online, jev_ms = self._jev_status()
        state = UiState(
            tick=tick,
            extra={
                **extra,
                "demo_active": demo_active,
                "has_classifier": self.classifier is not None,
                "has_vision": self.vision is not None,
            },
            surface=self._last_surface,
            app=self._last_app,
            browser_connected=self._browser_sockets > 0,
            browser_last_event_ms=self._browser_last_ms,
            cache=cache[:CACHE_ROWS],
            goal_history=list(self.cfg.data["goal_history"]),
            thresholds={
                **{k: float(getattr(self.engine.settings, k)) for k in ("dwell_sec", "afk_sec", "trust_conf")},
                "yaw_max": float(self.cfg.data["vision"].get("yaw_max", 20.0)),
            },
            camera_on=self.cfg.data["camera_on"],
            jev_online=jev_online,
            jev_last_ms=jev_ms,
        )
        try:
            self._on_update(state)
        except Exception:
            log.exception("on_update failed")

    # ------------------------------------------------------------ commands

    async def handle_command(self, msg: dict[str, Any]) -> None:
        """UI command dispatcher; publishes a fresh state afterwards (see EngineHandle.command)."""
        cmd = msg.get("cmd")
        now = self.clock.now_ms()
        if cmd == "set_goal":
            self._set_goal(str(msg.get("goal", "")).strip()[:200])
        elif cmd == "toggle_camera":
            self.cfg.data["camera_on"] = not self.cfg.data["camera_on"]
            self.cfg.save()
            if self.vision is not None:
                self.vision.set_enabled(self.cfg.data["camera_on"] and not self.engine.camera_released)
        elif cmd == "calibrate":
            calibrate = getattr(self.vision, "calibrate", None)
            if calibrate is not None:
                calibrate()
        elif cmd == "snooze":
            self.engine.snooze(now)
            self.cfg.data["snooze_until_ms"] = self.engine.snooze_until_ms
            self.cfg.save()
        elif cmd == "reclassify" and msg.get("category") in CATEGORIES and self._last_key:
            self._reclassify(self._last_key, msg["category"])
        elif cmd == "exclude" and self._last_surface is not None:
            s = self._last_surface
            self.cfg.exclude_surface(app=s.app, domain=s.domain if s.kind == "browser" else None)
        elif cmd == "set_threshold":
            self._set_threshold(str(msg.get("name")), msg.get("value"))
        elif cmd == "demo":
            self._demo(str(msg.get("which")), now)
        else:
            log.debug("ignored command %r", cmd)
            return
        if not self.demo.active:  # a running demo publishes its own ticks
            await self._safe_live_tick()

    def _set_goal(self, goal: str) -> None:
        self.cfg.remember_goal(goal)
        self.engine.reset_dwell()
        if self.classifier is not None:
            self.classifier.on_goal_changed(goal)

    def _reclassify(self, key: str, category: str) -> None:
        """Persist a user override; it beats the classifier, cache and local tier."""
        self.engine.set_override(key, category)  # type: ignore[arg-type]  # validated against CATEGORIES
        self.cfg.data["overrides"][key] = category
        self.cfg.save()
        set_override = getattr(self.classifier, "set_override", None)
        if set_override is not None:
            set_override(key, category)

    def _set_threshold(self, name: str, value: object) -> None:
        bounds = THRESHOLD_BOUNDS.get(name)
        if bounds is None or not isinstance(value, int | float):
            return
        v = min(max(float(value), bounds[0]), bounds[1])
        if name == "yaw_max":
            self.cfg.data["vision"][name] = v
        else:
            setattr(self.engine.settings, name, v)
            self.cfg.data["thresholds"][name] = v
        self.cfg.save()

    def _demo(self, which: str, now: int) -> None:
        if which == "D":
            self.demo.start(self._emit_demo)
        elif which == "G":
            self.demo.stop()
            self._set_goal(demo.G_GOAL)
            self._synthetic = (demo.G_SURFACE, now + G_SURFACE_HOLD_MS)
        elif which == "R":
            self.demo.stop()
            self._synthetic = None
            self.engine.reset()
            self.engine.snooze_until_ms = 0
            self.cfg.data["snooze_until_ms"] = 0
            self.cfg.save()
            if self.vision is not None:
                self.vision.set_enabled(self.cfg.data["camera_on"])

    async def _emit_demo(self, tick: Tick, effects: Effects, extra: dict[str, Any]) -> None:
        """Publish one scripted demo tick; only the escalation toast side effect is real."""
        if effects.toast is not None:
            show_toast(*effects.toast)
        self._publish(tick, extra, demo_active=True)


def _build_classifier(directory: Path, settings: dict[str, Any]) -> Classifier | None:
    try:
        from focus_tracker.classifier import build_classifier

        return build_classifier(directory, settings)
    except Exception as exc:  # noqa: BLE001 - optional component, degrade gracefully
        log.warning("classifier unavailable, running without categories: %s", exc)
        return None


def _build_vision(settings: dict[str, Any]) -> VisionLike | None:
    try:
        from focus_tracker.vision import VisionTracker

        return VisionTracker(settings)
    except Exception as exc:  # noqa: BLE001 - optional component, degrade gracefully
        log.warning("vision unavailable, input-only mode: %s", exc)
        return None


def build_runtime() -> Runtime:
    """Load config and env, set up logging, build optional components, return the wired runtime."""
    directory = data_dir()
    load_env(directory)
    handler = logging.handlers.RotatingFileHandler(directory / "focus-tracker.log", maxBytes=500_000, backupCount=2)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(), handler],
    )
    cfg = ConfigStore(directory)
    cfg.data["vision"]["data_dir"] = str(directory)  # vision persists its calibration baseline here
    return Runtime(cfg, _build_classifier(directory, cfg.data["classifier"]), _build_vision(cfg.data["vision"]))


def run_headless() -> None:
    """Run the engine without a window, logging each UiState. Debug fallback."""
    import time

    from focus_tracker.engine_thread import EngineThread

    def log_state(state: UiState) -> None:
        t = state.tick
        log.info("%s %s pi=%d goal=%r", t.state, t.surface_label, t.pi, t.goal)

    engine = EngineThread()
    engine.start(log_state)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        engine.stop()
