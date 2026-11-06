"""FakeEngine: scripted UiStates plus a synthetic camera frame, for developing the window without the real engine."""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np

from focus_tracker.schema import (
    AppSignal,
    CameraStatus,
    FrameSnapshot,
    Surface,
    Tick,
    UiState,
    VisionEvent,
)
from focus_tracker.ui.frames import (
    FACE_OVAL,
    LEFT_BROW,
    LEFT_BROW_UPPER,
    LEFT_EYE,
    LIPS_INNER,
    LIPS_OUTER,
    NOSE_TIP,
    RIGHT_BROW,
    RIGHT_BROW_UPPER,
    RIGHT_EYE,
)

CYCLE_SEC = 1500
TICK_SEC = 2.0
GOAL = "Write the spec for the hackathon project"


@dataclass(frozen=True, slots=True)
class _Segment:
    start: int
    end: int
    kind: str  # "app" or "browser"
    label: str
    process: str
    title: str
    url: str | None
    category: str
    source: str
    confidence: float


# One 25 minute loop: work, a distraction blip, an away stretch, then a long YouTube slide.
SEGMENTS = (
    _Segment(0, 520, "app", "VS Code", "Code.exe", "spec.md - focus-tracker - Visual Studio Code", None, "productive", "jev", 0.97),
    _Segment(520, 690, "browser", "github.com", "chrome.exe", "Pull requests - focus-tracker", "github.com/ilia/focus-tracker/pulls", "productive", "cache", 0.93),
    _Segment(690, 730, "app", "Discord", "Discord.exe", "#general - Discord", None, "distraction", "jev", 0.91),
    _Segment(730, 860, "app", "Terminal", "WindowsTerminal.exe", "uv run pytest", None, "neutral", "local", 0.72),
    _Segment(860, 1282, "app", "VS Code", "Code.exe", "schema.py - focus-tracker - Visual Studio Code", None, "productive", "jev", 0.96),
    _Segment(1282, CYCLE_SEC, "browser", "youtube.com", "chrome.exe", "funny cats 10 hours - YouTube", "youtube.com/watch", "distraction", "jev", 1.0),
)
AWAY = (400, 470)


def _segment_at(t: float) -> _Segment:
    return next(s for s in SEGMENTS if s.start <= t < s.end)


def make_state(index: int, *, ts_ms: int, camera_on: bool = True) -> UiState:
    """Deterministic scenario state for tick number `index` (2 s per tick, looping every 25 min)."""
    t = (index * TICK_SEC) % CYCLE_SEC
    seg = _segment_at(t)
    away = AWAY[0] <= t < AWAY[1]
    present = camera_on and not away
    yaw = 0.0 if not present else 5 * math.sin(t / 7) + (26 if seg.label == "Discord" and t % 12 < 8 else 0) - 3
    dwell = (t - seg.start) % 90 if seg.category == "distraction" else 0.0
    if away:
        state = "afk"
    elif seg.category == "distraction":
        state = "distracted"
    else:
        state = "focused"
    nudge = "That is 60 s on YouTube. Back to the spec?" if seg.category == "distraction" and 60 <= dwell < 64 else None
    page = seg.kind == "browser"
    surface = Surface(
        kind="browser" if page else "app",
        app="Google Chrome" if page else seg.label,
        window_title=seg.title,
        domain=seg.label if page else None,
        url=("https://" + seg.url) if seg.url else None,
        page_title=seg.title if page else None,
    )
    tick = Tick(
        state=state,  # type: ignore[arg-type]
        surface_kind=surface.kind,
        surface_label=seg.label,
        category=seg.category,  # type: ignore[arg-type]
        category_source=seg.source,  # type: ignore[arg-type]
        confidence=seg.confidence,
        classification_pending=False,
        goal=GOAL,
        camera=CameraStatus(on=camera_on, present=present, yaw=yaw, pitch=6.0 if present else 0.0, degraded=False),
        pi=64,
        nudge=nudge,
        ts=ts_ms,
    )
    return UiState(
        tick=tick,
        extra={
            "dwell_sec": dwell,
            "dwell_limit_sec": 60.0,
            "focused_sec": 41 * 60,
            "input_idle_sec": 400.0 if away else 2.0,
            "nudge_text": nudge,
            "baseline": "calibrated 14:01",
        },
        surface=surface,
        app=AppSignal(app=seg.label, process=seg.process, title=seg.title, input_idle_sec=2.0, ts=ts_ms),
        browser_connected=True,
        browser_last_event_ms=ts_ms - 600,
        cache=[
            {"key": f"{seg.label}|{GOAL[:12]}", "label": seg.label, "category": seg.category, "source": seg.source, "confidence": seg.confidence, "ts": ts_ms - 40_000},
            {"key": "github.com|goal", "label": "github.com", "category": "productive", "source": "cache", "confidence": 0.93, "ts": ts_ms - 600_000},
        ],
        goal_history=[GOAL, "Edit the demo video", "Triage inbox"],
        thresholds={"dwell_sec": 60.0, "afk_sec": 180.0, "trust_conf": 0.8, "yaw_max": 20.0},
        camera_on=camera_on,
        jev_online=True,
        jev_last_ms=203.0,
    )


# ---------------------------------------------------------------- synthetic frame


def _mesh_landmarks(cx: float, cy: float, rx: float, ry: float, yaw: float) -> np.ndarray:
    """478 points: contour indices laid on face-shaped curves, the rest scattered inside the oval."""
    rng = np.random.default_rng(7)
    ang = rng.uniform(0, 2 * np.pi, 478)
    rad = np.sqrt(rng.uniform(0, 1, 478)) * 0.92
    pts = np.stack([cx + rx * rad * np.cos(ang), cy + ry * rad * np.sin(ang)], axis=1).astype(np.float32)
    shift = yaw * 0.9  # pixels the features slide sideways when the head turns

    def loop(idx: tuple[int, ...], x: float, y: float, w: float, h: float, start: float = 0.0) -> None:
        for k, i in enumerate(idx):
            a = start + 2 * np.pi * k / len(idx)
            pts[i] = (cx + shift + x + w * np.cos(a), cy + y + h * np.sin(a))

    def arc(idx: tuple[int, ...], x: float, y: float, w: float, h: float) -> None:
        for k, i in enumerate(idx):
            f = k / (len(idx) - 1)
            pts[i] = (cx + shift + x + w * (f - 0.5) * 2, cy + y - h * math.sin(math.pi * f))

    loop(FACE_OVAL, 0, 0, rx, ry, start=-np.pi / 2)
    pts[list(FACE_OVAL)] -= np.array([shift * 0.4, 0], dtype=np.float32)
    loop(LEFT_EYE, rx * 0.42, -ry * 0.18, rx * 0.2, ry * 0.07)
    loop(RIGHT_EYE, -rx * 0.42, -ry * 0.18, rx * 0.2, ry * 0.07)
    loop(LIPS_OUTER, 0, ry * 0.5, rx * 0.34, ry * 0.12)
    loop(LIPS_INNER, 0, ry * 0.5, rx * 0.26, ry * 0.05)
    arc(LEFT_BROW, rx * 0.42, -ry * 0.42, rx * 0.24, ry * 0.05)
    arc(RIGHT_BROW, -rx * 0.42, -ry * 0.42, rx * 0.24, ry * 0.05)
    arc(LEFT_BROW_UPPER, rx * 0.42, -ry * 0.48, rx * 0.24, ry * 0.05)
    arc(RIGHT_BROW_UPPER, -rx * 0.42, -ry * 0.48, rx * 0.24, ry * 0.05)
    pts[NOSE_TIP] = (cx + shift * 1.6, cy + ry * 0.2)
    return pts


def make_frame(ts_ms: int, *, face: bool = True, yaw: float = -4.0, w: int = 640, h: int = 480) -> FrameSnapshot:
    """A cartoon desk scene (gradient wall, head, shoulders) with matching detector output."""
    yy, xx = np.mgrid[0:h, 0:w]
    img = np.zeros((h, w, 3), np.float32)
    img[..., 0] = 38 + 30 * (yy / h)
    img[..., 1] = 48 + 34 * (yy / h) + 10 * (xx / w)
    img[..., 2] = 62 + 40 * (yy / h)
    cx, cy, rx, ry = w * 0.5 + yaw * 2, h * 0.43, w * 0.15, h * 0.26
    shoulders = ((xx - w * 0.5) / (w * 0.38)) ** 2 + ((yy - h * 1.02) / (h * 0.3)) ** 2 < 1
    img[shoulders] = (58, 74, 104)
    if face:
        head = ((xx - cx) / rx) ** 2 + ((yy - cy) / ry) ** 2 < 1
        img[head] = (214, 166, 140)
    img += np.random.default_rng(ts_ms % 997).normal(0, 2.5, img.shape)  # sensor noise so frames visibly change
    image = np.clip(img, 0, 255).astype(np.uint8)
    if not face:
        return FrameSnapshot(image, None, None, None, None, None, ts_ms)
    lm = _mesh_landmarks(cx, cy, rx, ry, yaw)
    nose = (float(lm[NOSE_TIP][0]), float(lm[NOSE_TIP][1]))
    axes = (
        (nose[0] + 70, nose[1] - 4),
        (nose[0] + 3, nose[1] + 66),
        (nose[0] + yaw * 2.4, nose[1] - 60),
    )
    box = (cx - rx * 1.2, cy - ry * 1.15, rx * 2.4, ry * 2.3)
    return FrameSnapshot(image, lm, 0.97, box, nose, axes, ts_ms)


class _FakeVision:
    """VisionLike stand-in that renders a fresh synthetic frame at 8 fps."""

    def __init__(self) -> None:
        self.enabled = True
        self.face = True

    def start(self) -> None: ...
    def stop(self) -> None: ...
    def calibrate(self) -> None: ...

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled

    def latest(self) -> VisionEvent | None:
        if not self.enabled:
            return None
        return VisionEvent(present=self.face, facing=self.face, yaw=-4.0, pitch=6.0, fps=8.0, degraded=False, ts=int(time.time() * 1000))

    def latest_frame(self) -> FrameSnapshot | None:
        if not self.enabled:
            return None
        ts = int(time.time() * 8) * 125  # advances 8 times a second
        return make_frame(ts, face=self.face, yaw=-4 + 6 * math.sin(ts / 1500))


class FakeEngine:
    """EngineHandle that loops the scripted scenario in real time and echoes commands to stdout."""

    def __init__(self) -> None:
        self._vision = _FakeVision()
        self._on_update: Callable[[UiState], None] | None = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._index = 1240  # start near the YouTube slide so a nudge shows up soon
        self.commands: list[dict[str, Any]] = []

    def start(self, on_update: Callable[[UiState], None]) -> None:
        self._on_update = on_update
        threading.Thread(target=self._run, name="fake-engine", daemon=True).start()

    def _run(self) -> None:
        while not self._stop.is_set():
            assert self._on_update is not None
            self._on_update(make_state(self._index, ts_ms=int(time.time() * 1000), camera_on=self._vision.enabled))
            self._index += 1
            self._wake.wait(TICK_SEC)
            self._wake.clear()

    def command(self, msg: dict[str, Any]) -> None:
        self.commands.append(msg)
        if msg.get("cmd") == "toggle_camera":
            self._vision.set_enabled(not self._vision.enabled)
        self._wake.set()

    def vision(self) -> _FakeVision:
        return self._vision

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
