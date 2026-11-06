"""Qt application: window, tray icon, and the bridge from the engine thread."""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from focus_tracker.schema import EngineHandle, UiState
from focus_tracker.ui import theme
from focus_tracker.ui.fake import FakeEngine
from focus_tracker.ui.icon import scan_eye_icon, state_icon
from focus_tracker.ui.window import MainWindow

log = logging.getLogger("focus_tracker.ui")


class Bridge(QObject):
    """The engine calls `push` from its own thread; the queued signal lands on the Qt thread."""

    updated = Signal(object)

    def push(self, state: UiState) -> None:
        self.updated.emit(state)


def make_engine() -> EngineHandle:
    """The real EngineThread, or the FakeEngine when it cannot be imported."""
    try:
        from focus_tracker.engine_thread import EngineThread
    except ImportError:
        log.warning("engine_thread unavailable, running the UI on FakeEngine")
        return FakeEngine()
    return EngineThread()


class Tray:
    """System tray icon with Show, Snooze, Camera toggle and Quit."""

    def __init__(self, window: MainWindow, engine: EngineHandle, quit_app: Callable[[], None]) -> None:
        self.icon = QSystemTrayIcon(scan_eye_icon(theme.DIM), window)
        self._state_name = ""
        menu = QMenu()
        show = QAction("Show", menu)
        show.triggered.connect(self._show(window))
        snooze = QAction("Snooze 10 min", menu)
        snooze.triggered.connect(lambda: engine.command({"cmd": "snooze"}))
        self.camera = QAction("Camera off", menu)
        self.camera.triggered.connect(lambda: engine.command({"cmd": "toggle_camera"}))
        quit_ = QAction("Quit", menu)
        quit_.triggered.connect(quit_app)
        for a in (show, snooze, self.camera):
            menu.addAction(a)
        menu.addSeparator()
        menu.addAction(quit_)
        self._menu = menu
        self.icon.setContextMenu(menu)
        self.icon.activated.connect(
            lambda reason: self._show(window)() if reason == QSystemTrayIcon.ActivationReason.Trigger else None
        )
        self.icon.setToolTip("Focus Tracker")
        self.icon.show()

    @staticmethod
    def _show(window: MainWindow) -> Callable[[], None]:
        def go() -> None:
            window.showNormal()
            window.raise_()
            window.activateWindow()

        return go

    def update(self, ui: UiState) -> None:
        if ui.tick.state != self._state_name:
            self._state_name = ui.tick.state
            self.icon.setIcon(state_icon(ui.tick.state))
        self.icon.setToolTip(f"Focus Tracker: {ui.tick.state.replace('_', ' ')} · {ui.tick.surface_label}")
        self.camera.setText("Camera off" if ui.camera_on else "Camera on")


def run(engine: EngineHandle | None = None) -> int:
    """Launch the app and block until quit."""
    app = QApplication.instance() or QApplication(sys.argv)
    assert isinstance(app, QApplication)
    app.setApplicationName("Focus Tracker")
    app.setStyleSheet(theme.STYLESHEET)
    app.setFont(theme.body_font(13))
    app.setQuitOnLastWindowClosed(False)

    engine = engine or make_engine()
    window = MainWindow(engine)
    bridge = Bridge()
    bridge.updated.connect(window.apply)

    tray: Tray | None = None
    if QSystemTrayIcon.isSystemTrayAvailable():
        tray = Tray(window, engine, lambda: quit_app())
        bridge.updated.connect(tray.update)
        window.on_close_to_tray = lambda: True

    def quit_app() -> None:
        window.quitting = True
        engine.stop()
        if tray is not None:
            tray.icon.hide()
        app.quit()

    app.aboutToQuit.connect(engine.stop)
    if tray is None:
        app.lastWindowClosed.connect(quit_app)
    engine.start(bridge.push)
    window.show()
    return app.exec()
