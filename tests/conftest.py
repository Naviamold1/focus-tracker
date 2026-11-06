"""Shared helpers: a virtual-time driver for the decision engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from focus_tracker.decision import DecisionEngine, Effects, EngineInputs
from focus_tracker.schema import Category, Classification, Surface, Tick, VisionEvent

STEP_SEC = 2


def classification(
    category: Category | None, *, key: str = "k", confidence: float | None = 0.95, **kw: Any
) -> Classification:
    """A resolved Jev classification unless overridden (pending=True etc. via kw)."""
    kw.setdefault("source", "jev")
    kw.setdefault("pending", False)
    return Classification(key=key, category=category, confidence=confidence, **kw)


def vision(*, present: bool = True, facing: bool | None = True, degraded: bool = False) -> VisionEvent:
    return VisionEvent(present=present, facing=facing, yaw=0.0, pitch=0.0, fps=8.0, degraded=degraded, ts=0)


YOUTUBE = Surface(kind="browser", app="Google Chrome", window_title="w", domain="youtube.com", page_title="cats")
CODE = Surface(kind="app", app="Visual Studio Code", window_title="spec.md")


@dataclass
class Driver:
    """Steps an engine every 2 virtual seconds with mutable 'current' inputs."""

    engine: DecisionEngine
    now: int = 0
    goal: str = "write the spec"
    surface: Surface | None = CODE
    cls: Classification | None = field(default_factory=lambda: classification("productive"))
    vis: VisionEvent | None = field(default_factory=vision)
    camera_on: bool = True
    idle: float = 1.0
    nudges: list[tuple[int, str]] = field(default_factory=list)  # (time_s, level)

    def advance(self, seconds: int, **changes: Any) -> list[tuple[Tick, Effects]]:
        """Apply input changes (goal, surface, cls, vis, camera_on, idle) then run for `seconds`."""
        for name, value in changes.items():
            setattr(self, name, value)
        out = []
        for _ in range(seconds // STEP_SEC):
            inp = EngineInputs(self.goal, self.surface, self.cls, self.vis, self.camera_on, self.idle)
            tick, effects = self.engine.step(inp, self.now)
            if effects.nudge_level:
                self.nudges.append((self.now // 1000, effects.nudge_level))
            out.append((tick, effects))
            self.now += STEP_SEC * 1000
        return out

    @property
    def levels(self) -> list[str]:
        return [level for _, level in self.nudges]


@pytest.fixture
def drive() -> Driver:
    return Driver(DecisionEngine())
