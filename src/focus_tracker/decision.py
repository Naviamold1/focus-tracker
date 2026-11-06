"""Decision engine (spec §4.4): state machine, PI accounting, nudge ladder.

`DecisionEngine.step(inputs, now_ms)` is deterministic given its inputs and an
injected clock value, so tests and the demo drive it with virtual time.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, fields, replace
from typing import Any, Literal

from focus_tracker.schema import (
    CameraStatus,
    Category,
    Classification,
    State,
    Surface,
    Tick,
    VisionEvent,
)

HOUR_MS = 3_600_000
MAX_STEP_MS = 10_000  # a longer gap (sleep, stall) counts as 10 s, not the whole gap
PI_PRIOR_MS = 60_000  # smoothing so one early tick does not swing PI to 0 or 100
PI_HISTORY_POINTS = 1800  # 1 h at one tick per 2 s

NudgeLevel = Literal["soft", "escalated"]


@dataclass(slots=True)
class Settings:
    """Tunable thresholds (UI sliders). Seconds unless noted."""

    dwell_sec: float = 60.0
    escalate_sec: float = 120.0
    afk_sec: float = 180.0
    trust_conf: float = 0.8
    low_conf: float = 0.5
    nudge_cooldown_sec: float = 300.0
    max_per_hour: int = 3
    snooze_sec: float = 600.0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Settings:
        """Build from config, ignoring unknown keys."""
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass(frozen=True, slots=True)
class EngineInputs:
    """Everything the engine needs for one tick. `classification` is None when unavailable."""

    goal: str
    surface: Surface | None
    classification: Classification | None
    vision: VisionEvent | None
    camera_on: bool
    input_idle_sec: float


@dataclass(frozen=True, slots=True)
class Effects:
    """Side effects the caller must perform after a step."""

    nudge_level: NudgeLevel | None = None
    toast: tuple[str, str] | None = None  # (title, body), escalation only
    camera_release: bool | None = None  # True: release camera (afk), False: restore it


class DecisionEngine:
    """Stateful tick processor. One instance per timeline (live or demo)."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings()
        self.overrides: dict[str, Category] = {}
        self.snooze_until_ms = 0
        self.reset()

    # ------------------------------------------------------------ lifecycle

    def reset(self) -> None:
        """Clear PI, session clocks and nudge history (the R button). Keeps settings/overrides."""
        self._last_ms: int | None = None
        self._pi_events: deque[tuple[int, int, int]] = deque()  # (ts, sign, dt_ms)
        self._focus_ms = 0  # productive-and-attentive time inside the PI window
        self._distract_ms = 0
        self.focused_ms = 0
        self.afk_ms = 0
        self._history: deque[int] = deque(maxlen=PI_HISTORY_POINTS)
        self._last_nudge: dict[str, int] = {}
        self._nudges: deque[int] = deque()
        self._released = False
        self.nudge_seq = 0
        self.nudge_text = ""
        self.reset_dwell()

    def reset_dwell(self) -> None:
        """Forget the current distraction episode (surface left, goal changed, snoozed)."""
        self._dwell_ms = 0
        self._limit_ms = 0
        self._episode_nudge_ms: int | None = None
        self._escalated = False

    def snooze(self, now_ms: int) -> None:
        """Trust the user for `snooze_sec`: no nudges, dwell restarts afterwards."""
        self.snooze_until_ms = now_ms + int(self.settings.snooze_sec * 1000)
        self.reset_dwell()

    def set_override(self, key: str, category: Category | None) -> None:
        """User reclassification; beats every classifier source. None removes it."""
        if category is None:
            self.overrides.pop(key, None)
        else:
            self.overrides[key] = category
        self.reset_dwell()

    # ------------------------------------------------------------ step

    def step(self, inp: EngineInputs, now_ms: int) -> tuple[Tick, Effects]:
        """Advance one tick; returns the Tick to publish and the side effects to run."""
        s = self.settings
        dt = 0 if self._last_ms is None else min(max(now_ms - self._last_ms, 0), MAX_STEP_MS)
        self._last_ms = now_ms

        vis = inp.vision
        cam_usable = inp.camera_on and not self._released and vis is not None and not vis.degraded
        present = vis.present if (cam_usable and vis) else None
        facing = vis.facing if (cam_usable and vis) else None

        c = self._apply_override(inp.classification)
        cat: Category | Literal["unknown"] = c.category if c and c.category else "unknown"

        # afk: long idle and no face (or no camera to ask). Pauses clocks, releases camera.
        release: bool | None = None
        if inp.input_idle_sec > s.afk_sec and present is not True:
            if not self._released:
                self._released, release = True, True
            self.reset_dwell()
            self.afk_ms += dt
            return self._tick("afk", inp, c, cat, present, now_ms, None), Effects(camera_release=release)
        if self._released:
            self._released, release = False, False

        # observe-only: nothing to judge against
        if not inp.goal or inp.surface is None or c is None:
            self.reset_dwell()
            return self._tick("focused", inp, c, "unknown", present, now_ms, None), Effects(camera_release=release)

        pending = c.pending
        label = inp.surface.label
        looking_away = present is False or facing is False
        state: State = "focused"
        sign = 0
        nudge: str | None = None
        effects = Effects(camera_release=release)

        if c.category is None or pending:
            self.reset_dwell()  # unresolved counts as neutral; never nudges
        elif c.category == "distraction" and now_ms < self.snooze_until_ms:
            self.reset_dwell()  # snooze = trust
        elif c.category == "distraction" and looking_away:
            state = "quiet_thinking"
            self.reset_dwell()
        elif c.category == "distraction":
            sign = -1
            self._dwell_ms += dt
            self._limit_ms = self._dwell_limit_ms(c)
            state, nudge, effects = self._nudge_ladder(c, label, inp.goal, now_ms, release)
        else:
            self.reset_dwell()  # includes distraction -> productive switch (#6): no nudge
            if c.category == "productive" and not looking_away and c.source != "private":
                sign = 1
                self.focused_ms += dt

        self._record_pi(now_ms, sign, dt)
        return self._tick(state, inp, c, cat, present, now_ms, nudge), effects

    # ------------------------------------------------------------ nudge ladder

    def _dwell_limit_ms(self, c: Classification) -> int:
        """Dwell before a soft nudge: x2 for mid confidence or local verdicts, /2 when stale."""
        s = self.settings
        mid = c.confidence is not None and s.low_conf <= c.confidence < s.trust_conf
        limit = s.dwell_sec * 1000 * (2 if mid or c.source == "local" else 1)
        return int(limit / 2) if c.stale else int(limit)

    def _nudge_ladder(
        self, c: Classification, label: str, goal: str, now_ms: int, release: bool | None
    ) -> tuple[State, str | None, Effects]:
        """Soft nudge at the dwell limit, toast if still distracted escalate_sec later."""
        s = self.settings
        if self._episode_nudge_ms is None:
            if self._dwell_ms >= self._limit_ms and self._may_nudge(label, now_ms):
                self._episode_nudge_ms = now_ms
                self._last_nudge[label] = now_ms
                self._nudges.append(now_ms)
                self.nudge_seq += 1
                self.nudge_text = f"This looks like a detour from your goal: {label}"
                return "nudged", self.nudge_text, Effects("soft", camera_release=release)
            return "distracted", None, Effects(camera_release=release)
        if (
            not self._escalated
            and not c.stale
            and now_ms - self._episode_nudge_ms >= s.escalate_sec * 1000
        ):
            self._escalated = True
            self.nudge_seq += 1
            self.nudge_text = f"Still on {label}. Your goal: {goal}"
            return (
                "nudged",
                self.nudge_text,
                Effects("escalated", toast=("Focus Tracker", self.nudge_text), camera_release=release),
            )
        return "nudged", None, Effects(camera_release=release)

    def _may_nudge(self, label: str, now_ms: int) -> bool:
        """Anti-annoyance: 1 nudge per surface per cooldown, max N per hour globally."""
        s = self.settings
        last = self._last_nudge.get(label)
        if last is not None and now_ms - last < s.nudge_cooldown_sec * 1000:
            return False
        while self._nudges and now_ms - self._nudges[0] >= HOUR_MS:
            self._nudges.popleft()
        return len(self._nudges) < s.max_per_hour

    # ------------------------------------------------------------ helpers

    def _apply_override(self, c: Classification | None) -> Classification | None:
        if c is None or c.key not in self.overrides:
            return c
        return replace(
            c,
            category=self.overrides[c.key],
            source="override",
            confidence=None,
            pending=False,
            stale=False,
            remote_unreachable=False,
        )

    def _record_pi(self, now_ms: int, sign: int, dt: int) -> None:
        """Add this tick to the 60-minute PI window and drop expired samples."""
        if sign and dt:
            self._pi_events.append((now_ms, sign, dt))
            if sign > 0:
                self._focus_ms += dt
            else:
                self._distract_ms += dt
        while self._pi_events and now_ms - self._pi_events[0][0] >= HOUR_MS:
            _, old_sign, old_dt = self._pi_events.popleft()
            if old_sign > 0:
                self._focus_ms -= old_dt
            else:
                self._distract_ms -= old_dt

    @property
    def camera_released(self) -> bool:
        """True while the camera is released because of afk."""
        return self._released

    @property
    def pi(self) -> int:
        """0..100; 50 with no evidence, up with focused time, down with distracted time."""
        f, d = self._focus_ms, self._distract_ms
        return round(50 + 50 * (f - d) / (f + d + PI_PRIOR_MS))

    def _tick(
        self,
        state: State,
        inp: EngineInputs,
        c: Classification | None,
        cat: Category | Literal["unknown"],
        present: bool | None,
        now_ms: int,
        nudge: str | None,
    ) -> Tick:
        vis = inp.vision
        if state != "afk" and inp.goal and inp.surface is not None and c is not None:
            self._history.append(self.pi)
        surface = inp.surface
        return Tick(
            state=state,
            surface_kind=surface.kind if surface else "app",
            surface_label=surface.label if surface else "unknown",
            category=cat,
            category_source=c.source if c else None,
            confidence=c.confidence if c else None,
            classification_pending=c.pending if c else False,
            goal=inp.goal,
            camera=CameraStatus(
                on=inp.camera_on and not self._released,
                present=bool(present),
                yaw=vis.yaw if vis else 0.0,
                pitch=vis.pitch if vis else 0.0,
                degraded=vis.degraded if vis else False,
            ),
            pi=self.pi,
            nudge=nudge,
            ts=now_ms,
        )

    def snapshot(self) -> dict[str, Any]:
        """Extras for the UI that do not fit in Tick."""
        step = max(1, len(self._history) // 120)
        return {
            "dwell_sec": self._dwell_ms / 1000,
            "dwell_limit_sec": self._limit_ms / 1000,
            "pi_series": list(self._history)[::step],
            "focused_sec": self.focused_ms // 1000,
            "afk_sec": self.afk_ms // 1000,
            "snooze_until_ms": self.snooze_until_ms,
            "nudge_seq": self.nudge_seq,
            "nudge_text": self.nudge_text,
            "escalated": self._escalated,
        }
