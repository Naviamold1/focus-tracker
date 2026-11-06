"""Persistent user config (config.json in the per-user data dir) and env loading."""

from __future__ import annotations

import copy
import json
import logging
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

GOAL_HISTORY_MAX = 10

DEFAULTS: dict[str, Any] = {
    "goal": "",
    "goal_history": [],
    "camera_on": True,
    # Decision engine thresholds (UI sliders), see decision.Settings.
    "thresholds": {
        "dwell_sec": 60.0,
        "escalate_sec": 120.0,
        "afk_sec": 180.0,
        "trust_conf": 0.8,
        "low_conf": 0.5,
        "nudge_cooldown_sec": 300.0,
        "max_per_hour": 3,
        "snooze_sec": 600.0,
    },
    # classification key -> category; beats Jev, local tier and cache.
    "overrides": {},
    # Epoch ms until which nudges are snoozed.
    "snooze_until_ms": 0,
    # Passed through to build_classifier. The classifier owns its own keys;
    # user-excluded surfaces live under never_send so it sees them.
    "classifier": {"never_send": {"apps": [], "domains": []}},
    # Passed through to VisionTracker.
    "vision": {},
}


def default_data_dir(env: Mapping[str, str], platform: str, home: Path) -> Path:
    """Per-user data dir: %APPDATA%/focus-tracker on Windows, $XDG_DATA_HOME/focus-tracker elsewhere."""
    if platform == "win32":
        return Path(env.get("APPDATA", home)) / "focus-tracker"
    return Path(env.get("XDG_DATA_HOME") or home / ".local" / "share") / "focus-tracker"


def data_dir() -> Path:
    """User data dir; FOCUS_TRACKER_DATA overrides the platform default (used by tests)."""
    override = os.environ.get("FOCUS_TRACKER_DATA")
    base = Path(override) if override else default_data_dir(os.environ, sys.platform, Path.home())
    base.mkdir(parents=True, exist_ok=True)
    return base


def load_env(directory: Path) -> None:
    """Load TYPESAFE_API_KEY (and friends) from .env into the process env only."""
    from dotenv import load_dotenv

    load_dotenv(directory / ".env")
    load_dotenv()


def _merge(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    """Recursively overlay `extra` on `base` (extra wins), returning a new dict."""
    out = copy.deepcopy(base)
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


class ConfigStore:
    """In-memory config dict backed by config.json. Mutate `data`, then call save()."""

    def __init__(self, directory: Path) -> None:
        self.dir = directory
        self.path = directory / "config.json"
        self.data: dict[str, Any] = copy.deepcopy(DEFAULTS)
        if self.path.exists():
            try:
                self.data = _merge(DEFAULTS, json.loads(self.path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                log.warning("config.json unreadable, using defaults")

    def save(self) -> None:
        """Write atomically so a crash never leaves a half-written file."""
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def remember_goal(self, goal: str) -> None:
        """Set the current goal and move it to the front of the history (max 10)."""
        self.data["goal"] = goal
        if goal:
            history = [g for g in self.data["goal_history"] if g != goal]
            self.data["goal_history"] = [goal, *history][:GOAL_HISTORY_MAX]
        self.save()

    def exclude_surface(self, *, app: str | None, domain: str | None) -> None:
        """Add a surface to the classifier's never-send list (domain wins for browsers)."""
        never_send = self.data["classifier"].setdefault("never_send", {"apps": [], "domains": []})
        field, value = ("domains", domain) if domain else ("apps", app)
        if value and value not in never_send.setdefault(field, []):
            never_send[field].append(value)
            self.save()
