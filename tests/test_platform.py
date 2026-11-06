"""Pure platform helpers: process-name normalization, labels, Wayland detection, data dir."""

from __future__ import annotations

from pathlib import Path

import pytest

from focus_tracker.config import default_data_dir
from focus_tracker.watcher import friendly_label, is_native_wayland, normalize_process
from focus_tracker.watcher._wayland import parse_idletime


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("Chrome.EXE", "chrome"), ("firefox", "firefox"), ("  Code.exe ", "code"), ("", None), (None, None)],
)
def test_normalize_process(raw: str | None, expected: str | None) -> None:
    assert normalize_process(raw) == expected


def test_labels_cover_windows_and_linux_names_and_fall_back_to_title_case() -> None:
    assert friendly_label("chrome.exe") == friendly_label("google-chrome") == "Google Chrome"
    assert friendly_label("gnome-terminal-server") == "GNOME Terminal"
    assert friendly_label("my_tool.exe") == "My Tool"
    assert friendly_label(None) is None


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({"WAYLAND_DISPLAY": "wayland-0"}, True),
        ({"XDG_SESSION_TYPE": "wayland"}, True),
        ({"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0"}, False),  # XWayland: ask X
        ({"DISPLAY": ":0", "XDG_SESSION_TYPE": "x11"}, False),
        ({}, False),
    ],
)
def test_is_native_wayland(env: dict[str, str], expected: bool) -> None:
    assert is_native_wayland(env) is expected


def test_data_dir_per_platform() -> None:
    home = Path("/home/u")
    assert default_data_dir({"APPDATA": "/ad"}, "win32", home) == Path("/ad/focus-tracker")
    assert default_data_dir({"XDG_DATA_HOME": "/xdg"}, "linux", home) == Path("/xdg/focus-tracker")
    assert default_data_dir({}, "linux", home) == home / ".local/share/focus-tracker"


def test_parse_idletime() -> None:
    assert parse_idletime("(uint64 4200,)\n") == 4.2
    assert parse_idletime("garbage") is None
