"""Shared message types and component interfaces (spec §3.3).

This module is the contract between components. Every component imports from
here; nothing imports another component's internals. Change it deliberately.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

SCHEMA_VERSION = 1

Category = Literal["productive", "neutral", "distraction"]
# "private" = never-send surface (§4.2.6), always treated as neutral and excluded from PI.
ClassificationSource = Literal["jev", "cache", "local", "override", "private"]
State = Literal["focused", "distracted", "nudged", "afk", "quiet_thinking"]
SurfaceKind = Literal["browser", "app"]


# ---------------------------------------------------------------- sensor input


@dataclass(frozen=True, slots=True)
class BrowserEvent:
    """Extension -> engine over WS ("browser" channel). URL has query and fragment stripped."""

    url: str | None
    domain: str | None  # eTLD+1, None for chrome:// and new tab
    page_title: str | None
    audible: bool
    incognito: bool
    ts: int  # epoch ms UTC
    v: int = SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class AppSignal:
    """Published by the app watcher at 1 Hz."""

    app: str | None  # human label, e.g. "Discord", "Google Chrome"
    process: str | None  # executable name, e.g. "chrome.exe"
    title: str | None  # foreground window title
    input_idle_sec: float
    ts: int
    v: int = SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class VisionEvent:
    """Published by the vision thread. Never carries pixels."""

    present: bool
    facing: bool | None  # None = unknown (camera off, degraded, warming up)
    yaw: float  # degrees, already offset by calibration baseline
    pitch: float
    fps: float
    degraded: bool
    ts: int
    v: int = SCHEMA_VERSION


# ---------------------------------------------------------------- fused surface


@dataclass(frozen=True, slots=True)
class Surface:
    """What the user is looking at, after fusing AppSignal with the latest BrowserEvent."""

    kind: SurfaceKind
    app: str | None
    window_title: str | None
    domain: str | None = None  # browser only
    url: str | None = None  # browser only, query/fragment stripped, depth-capped
    page_title: str | None = None  # browser only
    incognito: bool = False

    @property
    def label(self) -> str:
        """Short human label for the UI status strip."""
        if self.kind == "browser" and self.domain:
            return self.domain
        return self.app or "unknown"


@dataclass(frozen=True, slots=True)
class Classification:
    """Classifier -> decision engine."""

    key: str  # cache key, includes a goal hash (spec §10 #8, adopted)
    category: Category | None  # None while unresolved
    source: ClassificationSource | None
    confidence: float | None  # 0..1
    pending: bool  # True while a remote (Jev) answer is outstanding; nudges are blocked
    stale: bool = False  # coalesced domain-level answer (§4.2.4): soft nudge only, never escalates
    remote_unreachable: bool = False  # Jev confirmed down: local verdicts may nudge (doubled dwell)
    ts: int = 0
    v: int = SCHEMA_VERSION


# ---------------------------------------------------------------- engine output


@dataclass(frozen=True, slots=True)
class CameraStatus:
    on: bool
    present: bool
    yaw: float
    pitch: float
    degraded: bool


@dataclass(frozen=True, slots=True)
class Tick:
    """Engine -> dashboard over WS ("state" channel), every 2 s."""

    state: State
    surface_kind: SurfaceKind
    surface_label: str
    category: Category | Literal["unknown"] | None
    category_source: ClassificationSource | None
    confidence: float | None
    classification_pending: bool
    goal: str
    camera: CameraStatus
    pi: int
    nudge: str | None = None  # nudge text fired on this tick, if any
    ts: int = 0
    v: int = SCHEMA_VERSION


# ---------------------------------------------------------------- interfaces


class Classifier(Protocol):
    """The only classification API the rest of the engine sees (spec §4.2.2)."""

    def classify(self, goal: str, surface: Surface) -> Classification:
        """Non-blocking. Returns the best current answer and schedules a remote
        request in the background for novel surfaces (result has pending=True
        until it lands). Callers re-call each tick to pick up the answer.
        With an empty goal, returns category=None and makes no remote call."""
        ...

    def on_goal_changed(self, goal: str) -> None: ...

    def set_override(self, key: str, category: Category) -> None:
        """User reclassification; persisted, beats every other source (scoped to the key's goal)."""
        ...

    def cache_entries(self) -> list[dict[str, object]]:
        """Rows for the dashboard cache inspector: key, category, source, confidence, ts, label."""
        ...

    def clear_cache(self) -> None: ...


class AppWatcherLike(Protocol):
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def latest(self) -> AppSignal | None: ...


@dataclass(frozen=True, slots=True)
class FrameSnapshot:
    """Latest camera frame plus what the detector found, for the local Qt window only.

    Never written to disk, never sent over any socket. The window paints it and drops it."""

    image: Any  # numpy uint8 array, HxWx3 RGB (already converted from BGR), downscaled to <= 640 wide
    landmarks: Any | None  # numpy float32 (478, 2), pixel coords in `image`; None when no face
    face_score: float | None  # detector presence confidence 0..1
    box: tuple[float, float, float, float] | None  # x, y, w, h in image pixels
    nose: tuple[float, float] | None  # nose tip, image pixels (origin of the pose axes)
    axes: tuple[tuple[float, float], tuple[float, float], tuple[float, float]] | None
    # ^ projected endpoints (image pixels) of the head's x (red), y (green), z/forward (blue) axes
    ts: int


class VisionLike(Protocol):
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def set_enabled(self, enabled: bool) -> None:
        """Camera toggle. Disabled releases the device (LED off)."""
        ...
    def calibrate(self) -> None:
        """Start a 3 s 'look at the screen' baseline capture (spec §10 #10, adopted)."""
        ...
    def latest(self) -> VisionEvent | None: ...
    def latest_frame(self) -> FrameSnapshot | None:
        """Most recent inferred frame with overlay data; None when the camera is off or unavailable."""
        ...


@dataclass(frozen=True, slots=True)
class UiState:
    """Engine -> Qt window, once per 2 s tick (and after each command)."""

    tick: Tick
    extra: dict[str, Any]  # dwell_sec, dwell_limit_sec, pi_series, focused_sec, input_idle_sec, nudge_text, ...
    surface: Surface | None  # full fused surface (process, window title, url path) for the foreground panel
    app: AppSignal | None  # raw watcher sample (process name, idle)
    browser_connected: bool  # extension socket open
    browser_last_event_ms: int | None
    cache: list[dict[str, object]]  # classifier cache rows, newest first
    goal_history: list[str]
    thresholds: dict[str, float]
    camera_on: bool
    jev_online: bool
    jev_last_ms: float | None  # last Jev round-trip latency


class EngineHandle(Protocol):
    """How the Qt window talks to the engine, which runs its asyncio loop on a background thread."""

    def start(self, on_update: Callable[[UiState], None]) -> None:
        """Start the engine thread. `on_update` is called FROM THE ENGINE THREAD; the UI must
        marshal it to the Qt thread (e.g. emit a Qt signal)."""
        ...

    def command(self, msg: dict[str, Any]) -> None:
        """Thread-safe. Same command dicts as before: set_goal, toggle_camera, calibrate, snooze,
        reclassify{category}, exclude, set_threshold{name,value}, demo{which: D|G|R}."""
        ...

    def vision(self) -> VisionLike | None:
        """Direct access for the camera view to poll latest_frame() at its own rate."""
        ...

    def stop(self) -> None: ...


@dataclass(slots=True)
class Clock:
    """Injectable time source so the decision engine and classifier are testable."""

    offset_ms: int = 0
    frozen_ms: int | None = field(default=None)

    def now_ms(self) -> int:
        if self.frozen_ms is not None:
            return self.frozen_ms
        import time

        return int(time.time() * 1000) + self.offset_ms
