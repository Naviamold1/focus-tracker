"""Foreground app watcher: 1 Hz poll through a per-platform backend, no admin needed.

Backends (each exposes `Backend.read()`):
- `_win32`: pywin32 + GetLastInputInfo
- `_x11`: EWMH via python-xlib (X11 and XWayland-focused X clients)
- `_wayland`: native Wayland has no foreground-window API; app/title are None,
  idle is best effort via GNOME Mutter's IdleMonitor.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from collections.abc import Mapping
from typing import NamedTuple, Protocol

from focus_tracker.schema import AppSignal

log = logging.getLogger(__name__)

# Normalized process name (lowercase, no ".exe") -> label.
# Anything else is title-cased from the normalized name.
FRIENDLY_APPS: dict[str, str] = {
    "chrome": "Google Chrome",
    "google-chrome": "Google Chrome",
    "google-chrome-stable": "Google Chrome",
    "chromium": "Chromium",
    "chromium-browser": "Chromium",
    "msedge": "Microsoft Edge",
    "microsoft-edge": "Microsoft Edge",
    "firefox": "Firefox",
    "firefox-bin": "Firefox",
    "brave": "Brave",
    "brave-browser": "Brave",
    "opera": "Opera",
    "vivaldi": "Vivaldi",
    "vivaldi-bin": "Vivaldi",
    "arc": "Arc",
    "code": "Visual Studio Code",
    "cursor": "Cursor",
    "discord": "Discord",
    "slack": "Slack",
    "spotify": "Spotify",
    "explorer": "File Explorer",
    "windowsterminal": "Windows Terminal",
    "acrord32": "Acrobat Reader",
    "acrobat": "Acrobat",
    "winword": "Word",
    "excel": "Excel",
    "powerpnt": "PowerPoint",
    "notepad": "Notepad",
    "overwatch": "Overwatch",
    "steam": "Steam",
    "devenv": "Visual Studio",
    "gnome-terminal-server": "GNOME Terminal",
    "konsole": "Konsole",
    "alacritty": "Alacritty",
    "kitty": "Kitty",
    "nautilus": "Files",
    "dolphin": "Dolphin",
    "thunar": "Thunar",
    "evince": "Document Viewer",
    "okular": "Okular",
    "soffice.bin": "LibreOffice",
    "obsidian": "Obsidian",
    "telegram-desktop": "Telegram",
}

# Normalized names of browsers that the extension can report on (used by fusion.py).
BROWSER_PROCESSES = frozenset(
    {
        "chrome",
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
        "msedge",
        "microsoft-edge",
        "firefox",
        "firefox-bin",
        "brave",
        "brave-browser",
        "opera",
        "vivaldi",
        "vivaldi-bin",
        "arc",
    }
)


def normalize_process(process: str | None) -> str | None:
    """Lowercase process name without a trailing '.exe', e.g. 'Chrome.EXE' -> 'chrome'."""
    if not process:
        return None
    name = process.strip().lower()
    return name.removesuffix(".exe") or None


def friendly_label(process: str | None) -> str | None:
    """Human label for a process name like 'chrome.exe' or 'google-chrome'."""
    name = normalize_process(process)
    if name is None:
        return None
    return FRIENDLY_APPS.get(name) or name.replace("_", " ").title()


def is_native_wayland(env: Mapping[str, str]) -> bool:
    """True for a Wayland session with no X server to ask (no DISPLAY).

    With DISPLAY set (XWayland) the X11 backend is used; it sees X clients only.
    """
    if env.get("DISPLAY"):
        return False
    return bool(env.get("WAYLAND_DISPLAY")) or env.get("XDG_SESSION_TYPE") == "wayland"


class Reading(NamedTuple):
    """One backend reading. process is the raw executable name ('chrome.exe', 'firefox')."""

    process: str | None
    title: str | None
    idle_sec: float


class Backend(Protocol):
    def read(self) -> Reading: ...

    def close(self) -> None: ...


def make_backend(env: Mapping[str, str] | None = None) -> Backend:
    """Backend for this OS and session. Call on the thread that will use it."""
    env = os.environ if env is None else env
    if sys.platform == "win32":
        from focus_tracker.watcher._win32 import Win32Backend

        return Win32Backend()
    from focus_tracker.watcher import _wayland

    if is_native_wayland(env):
        return _wayland.WaylandBackend()
    from focus_tracker.watcher._x11 import X11Backend

    try:
        return X11Backend(wayland_session=env.get("XDG_SESSION_TYPE") == "wayland")
    except Exception as exc:  # noqa: BLE001 - no usable X server: degrade to the Wayland stub
        log.warning("X11 unavailable (%s), falling back to the no-foreground backend", exc)
        return _wayland.WaylandBackend()


def sample(backend: Backend) -> AppSignal:
    """One AppSignal from a backend reading."""
    r = backend.read()
    return AppSignal(
        app=friendly_label(r.process),
        process=r.process,
        title=r.title,
        input_idle_sec=r.idle_sec,
        ts=int(time.time() * 1000),
    )


class AppWatcher:
    """Daemon thread that publishes the latest AppSignal once per second."""

    def __init__(self, interval_sec: float = 1.0) -> None:
        self._interval = interval_sec
        self._latest: AppSignal | None = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="app-watcher", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def latest(self) -> AppSignal | None:
        return self._latest

    def _run(self) -> None:
        try:
            backend = make_backend()
        except Exception:
            log.exception("watcher backend unavailable")
            return
        try:
            while not self._stop.is_set():
                try:
                    self._latest = sample(backend)
                except Exception:
                    log.exception("watcher sample failed")
                self._stop.wait(self._interval)
        finally:
            backend.close()
