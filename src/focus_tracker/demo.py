"""Demo scenarios (spec §4.6): D replays a 20-minute distraction arc, G flips the goal.

The D scenario runs through a real DecisionEngine on virtual time with scripted
classifications and vision, so it needs no camera, network or classifier.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass
from typing import Any

from focus_tracker.decision import DecisionEngine, Effects, EngineInputs, Settings
from focus_tracker.schema import Category, Classification, Surface, Tick, VisionEvent

log = logging.getLogger(__name__)

DEMO_GOAL = "write the spec for the hackathon project"
G_GOAL = "editing the demo reel"
G_SURFACE = Surface(
    kind="browser",
    app="Google Chrome",
    window_title="Celebrity breakups ranked: who cried hardest - Google Chrome",
    domain="starbuzzdaily.com",
    url="starbuzzdaily.com/gossip/breakups-ranked",
    page_title="Celebrity breakups ranked: who cried hardest",
)  # Jev: distraction at 1.00 for G_GOAL (probed 2026-11-06); a video site scored only 0.67
STEP_SEC = 2  # virtual seconds per engine tick, as in the live loop


@dataclass(frozen=True, slots=True)
class Segment:
    """One stretch of the scripted timeline."""

    seconds: int
    surface: Surface
    category: Category
    confidence: float
    facing: bool = True
    present: bool = True
    idle_start: float = 1.0  # input idle at segment start; grows 1 s/s when `idle_grows`
    idle_grows: bool = False


_CODE = Surface(kind="app", app="Visual Studio Code", window_title="spec.md - Visual Studio Code")
_YOUTUBE = Surface(
    kind="browser",
    app="Google Chrome",
    window_title="funny cats 10 hours - Google Chrome",
    domain="youtube.com",
    url="youtube.com/watch",
    page_title="funny cats 10 hours",
)
_DISCORD = Surface(kind="app", app="Discord", window_title="#general - Discord")

SCENARIO: tuple[Segment, ...] = (
    Segment(90, _CODE, "productive", 0.95),
    Segment(240, _YOUTUBE, "distraction", 0.91),  # soft nudge at 1:00, toast at 3:00
    Segment(90, _CODE, "productive", 0.95),  # back to work: dwell resets, no nudge
    Segment(180, _DISCORD, "distraction", 0.70),  # mid confidence: dwell doubled to 2:00
    Segment(120, _YOUTUBE, "distraction", 0.91, facing=False),  # looking away: quiet thinking
    Segment(120, _CODE, "productive", 0.95),
    Segment(360, _CODE, "productive", 0.95, present=False, idle_start=150, idle_grows=True),  # afk
)


def replay(
    segments: tuple[Segment, ...] = SCENARIO, settings: Settings | None = None
) -> Iterator[tuple[Tick, Effects, dict[str, Any]]]:
    """Yield (Tick, Effects, UI extras) for every 2 s virtual tick of the scenario."""
    engine = DecisionEngine(settings)
    now_ms = 0
    for seg in segments:
        cls = Classification(
            key=f"demo:{seg.surface.label}",
            category=seg.category,
            source="jev",
            confidence=seg.confidence,
            pending=False,
        )
        for t in range(0, seg.seconds, STEP_SEC):
            idle = seg.idle_start + (t if seg.idle_grows else 0)
            vision = VisionEvent(
                present=seg.present,
                facing=seg.facing if seg.present else None,
                yaw=-4.0 if seg.facing else 38.0,
                pitch=3.0,
                fps=8.0,
                degraded=False,
                ts=now_ms,
            )
            inputs = EngineInputs(DEMO_GOAL, seg.surface, cls, vision, True, idle)
            tick, effects = engine.step(inputs, now_ms)
            yield tick, effects, {**engine.snapshot(), "input_idle_sec": idle}
            now_ms += STEP_SEC * 1000


EmitFn = Callable[[Tick, Effects, dict[str, Any]], Awaitable[None]]


class DemoRunner:
    """Plays SCENARIO at accelerated speed and hands each tick to `emit`."""

    def __init__(self, speedup: float = 16.0) -> None:
        self._delay = STEP_SEC / speedup
        self._task: asyncio.Task[None] | None = None

    @property
    def active(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self, emit: EmitFn) -> None:
        """Begin (or restart) the D scenario."""
        self.stop()
        self._task = asyncio.create_task(self._run(emit))

    def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def _run(self, emit: EmitFn) -> None:
        for tick, effects, extra in replay():
            await emit(tick, effects, extra)
            await asyncio.sleep(self._delay)
        self._task = None


def register_hotkeys(trigger: Callable[[str], None]) -> None:
    """Global Ctrl+Alt+D/G/R hotkeys (plain letters would hijack typing). Failures are non-fatal.

    On Linux the `keyboard` package reads /dev/input and needs root, so hotkeys stay off
    for normal users (the window buttons still work).
    """
    if sys.platform.startswith("linux") and os.geteuid() != 0:
        log.info("global hotkeys disabled on Linux (need root); use the window buttons")
        return
    try:
        import keyboard

        for letter in ("D", "G", "R"):
            keyboard.add_hotkey(f"ctrl+alt+{letter.lower()}", trigger, args=(letter,))
    except Exception as exc:  # noqa: BLE001 - optional component, degrade gracefully
        log.warning("global hotkeys unavailable: %s", exc)


def unregister_hotkeys() -> None:
    """Remove the hotkeys added by `register_hotkeys`. Failures are non-fatal."""
    if sys.platform.startswith("linux") and os.geteuid() != 0:
        return
    try:
        import keyboard

        keyboard.unhook_all_hotkeys()
    except Exception as exc:  # noqa: BLE001 - optional component
        log.debug("hotkey cleanup failed: %s", exc)
