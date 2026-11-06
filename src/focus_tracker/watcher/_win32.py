"""Windows backend: pywin32 foreground window, kernel32 process image name, GetLastInputInfo."""

from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes
from pathlib import PureWindowsPath

import win32api
import win32gui
import win32process

from focus_tracker.watcher import Reading

log = logging.getLogger(__name__)

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.LPWSTR,
    ctypes.POINTER(wintypes.DWORD),
]


def _process_name(hwnd: int) -> str | None:
    """Executable file name owning the window, or None if it cannot be queried."""
    _, pid = win32process.GetWindowThreadProcessId(hwnd)
    handle = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        size = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if not _kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return None
        return PureWindowsPath(buf.value).name
    finally:
        _kernel32.CloseHandle(handle)


def input_idle_sec() -> float:
    """Seconds since the last keyboard or mouse input (GetLastInputInfo)."""
    elapsed_ms = (win32api.GetTickCount() - win32api.GetLastInputInfo()) & 0xFFFFFFFF
    return elapsed_ms / 1000


class Win32Backend:
    def read(self) -> Reading:
        hwnd = win32gui.GetForegroundWindow()
        process = title = None
        if hwnd:
            try:
                title = win32gui.GetWindowText(hwnd) or None
                process = _process_name(hwnd)
            except Exception:  # window vanished mid-query; report what we have
                log.debug("foreground query failed", exc_info=True)
        return Reading(process, title, input_idle_sec())

    def close(self) -> None:
        pass
