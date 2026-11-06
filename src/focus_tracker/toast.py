"""Escalation toasts, no extra deps: PowerShell on Windows, `notify-send` on Linux (no-op if absent)."""

from __future__ import annotations

import base64
import logging
import shutil
import subprocess
import sys

log = logging.getLogger(__name__)

# AppUserModelID of Windows PowerShell; toasts need a registered id to show.
_APP_ID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"
_CREATE_NO_WINDOW = 0x08000000


def _ps_quote(text: str) -> str:
    """Single-quote a string for PowerShell."""
    return "'" + text.replace("'", "''") + "'"


def _windows_toast(title: str, body: str) -> None:
    script = (
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, "
        "ContentType = WindowsRuntime] | Out-Null;"
        "$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
        "[Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
        "$n = $t.GetElementsByTagName('text');"
        f"$n.Item(0).AppendChild($t.CreateTextNode({_ps_quote(title)})) | Out-Null;"
        f"$n.Item(1).AppendChild($t.CreateTextNode({_ps_quote(body)})) | Out-Null;"
        "[Windows.UI.Notifications.ToastNotificationManager]::"
        f"CreateToastNotifier({_ps_quote(_APP_ID)}).Show("
        "[Windows.UI.Notifications.ToastNotification]::new($t))"
    )
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    subprocess.Popen(
        ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        creationflags=_CREATE_NO_WINDOW,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _linux_toast(title: str, body: str) -> None:
    notify_send = shutil.which("notify-send")
    if notify_send is None:
        return
    subprocess.Popen(
        [notify_send, "--app-name=Focus Tracker", "--", title, body],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def show_toast(title: str, body: str) -> None:
    """Fire-and-forget toast. Never raises; failures are logged."""
    try:
        if sys.platform == "win32":
            _windows_toast(title, body)
        elif sys.platform.startswith("linux"):
            _linux_toast(title, body)
    except OSError:
        log.warning("toast failed to launch", exc_info=True)
