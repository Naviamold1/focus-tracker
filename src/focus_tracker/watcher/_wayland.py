"""Native Wayland backend (best effort).

Wayland has no generic foreground-window API, so app and title are always None.
Idle comes from GNOME Mutter's IdleMonitor over `gdbus`; elsewhere it reports 0.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess

from focus_tracker.watcher import Reading

log = logging.getLogger(__name__)

_IDLE_CALL = [
    "gdbus", "call", "--session",
    "--dest", "org.gnome.Mutter.IdleMonitor",
    "--object-path", "/org/gnome/Mutter/IdleMonitor/Core",
    "--method", "org.gnome.Mutter.IdleMonitor.GetIdletime",
]  # fmt: skip


def parse_idletime(output: str) -> float | None:
    """Seconds from gdbus output like '(uint64 4200,)'; None if unparseable."""
    m = re.search(r"(?:uint64\s+)?(\d+)", output)
    return int(m.group(1)) / 1000 if m else None


def mutter_idle_sec() -> float | None:
    """Idle seconds from GNOME Mutter, or None when unavailable (not GNOME, no gdbus)."""
    if shutil.which("gdbus") is None:
        return None
    try:
        out = subprocess.run(_IDLE_CALL, capture_output=True, text=True, timeout=2, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return parse_idletime(out)


class WaylandBackend:
    def __init__(self) -> None:
        log.warning(
            "native Wayland session: the foreground app cannot be detected, so the app "
            "signal is empty. Use an X11 session for full support."
        )
        self._idle_ok = True

    def read(self) -> Reading:
        idle = mutter_idle_sec() if self._idle_ok else None
        if idle is None and self._idle_ok:
            self._idle_ok = False
            log.warning("idle time unavailable on this Wayland compositor (GNOME only); reporting 0")
        return Reading(None, None, idle or 0.0)

    def close(self) -> None:
        pass
