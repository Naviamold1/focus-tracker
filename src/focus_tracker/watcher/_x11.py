"""X11 backend: EWMH active window via python-xlib, idle via the XScreenSaver extension.

Works for X11 sessions and for X clients under XWayland. When a native Wayland
window is focused under XWayland, _NET_ACTIVE_WINDOW is unset and app/title are None.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from Xlib import X, display
from Xlib.error import XError

from focus_tracker.watcher import Reading
from focus_tracker.watcher._wayland import mutter_idle_sec

log = logging.getLogger(__name__)

_COMM_MAX = 15  # /proc/<pid>/comm is truncated to 15 chars


def process_name(pid: int) -> str | None:
    """Executable name for a pid: comm, or the exe basename when comm was truncated."""
    proc = Path("/proc") / str(pid)
    try:
        comm = (proc / "comm").read_text().strip()
    except OSError:
        return None
    if len(comm) >= _COMM_MAX:
        try:
            return (proc / "exe").resolve(strict=True).name
        except OSError:
            pass
    return comm or None


class X11Backend:
    def __init__(self, *, wayland_session: bool = False) -> None:
        self._d = display.Display()  # raises when there is no usable X server
        self._root = self._d.screen().root
        self._active = self._d.intern_atom("_NET_ACTIVE_WINDOW")
        self._net_name = self._d.intern_atom("_NET_WM_NAME")
        self._net_pid = self._d.intern_atom("_NET_WM_PID")
        # XScreenSaver idle only sees X clients under XWayland, so prefer Mutter there.
        self._mutter_idle = wayland_session
        self._xss_ok = self._d.has_extension("MIT-SCREEN-SAVER")
        if not self._xss_ok:
            log.warning("XScreenSaver extension missing: input idle will read 0")

    @staticmethod
    def _property(window: Any, atom: int) -> Any | None:
        prop = window.get_full_property(atom, X.AnyPropertyType)
        return prop.value if prop is not None else None

    def _foreground(self) -> tuple[str | None, str | None]:
        ids = self._property(self._root, self._active)
        if ids is None or len(ids) == 0 or not ids[0]:
            return None, None
        window = self._d.create_resource_object("window", int(ids[0]))
        title: str | None = None
        raw = self._property(window, self._net_name)
        if isinstance(raw, bytes):
            title = raw.decode("utf-8", "replace")
        elif (legacy := window.get_wm_name()) is not None:
            title = legacy if isinstance(legacy, str) else bytes(legacy).decode("latin-1")
        pids = self._property(window, self._net_pid)
        process = process_name(int(pids[0])) if pids is not None and len(pids) else None
        return process, title or None

    def _idle_sec(self) -> float:
        if self._mutter_idle:
            idle = mutter_idle_sec()
            if idle is not None:
                return idle
        if not self._xss_ok:
            return 0.0
        return self._d.screensaver_query_info(self._root).idle / 1000

    def read(self) -> Reading:
        try:
            process, title = self._foreground()
        except XError:  # window vanished mid-query
            log.debug("foreground query failed", exc_info=True)
            process = title = None
        return Reading(process, title, self._idle_sec())

    def close(self) -> None:
        self._d.close()
