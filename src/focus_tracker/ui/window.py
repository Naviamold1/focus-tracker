"""Main window: camera | foreground + verdict | goal + state, over the trace strip."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QRectF, QSize, QStringListModel, Qt, QTimer
from PySide6.QtGui import QCloseEvent, QColor, QFontMetrics, QPainter
from PySide6.QtWidgets import (
    QComboBox,
    QCompleter,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from focus_tracker.schema import EngineHandle, UiState
from focus_tracker.ui import theme
from focus_tracker.ui.camera_view import CameraView
from focus_tracker.ui.icon import scan_eye_icon
from focus_tracker.ui.settings_dialog import SettingsDialog
from focus_tracker.ui.traces import TraceStrip

STATE_WORD = {"focused": "FOCUSED", "distracted": "DISTRACTED", "nudged": "NUDGED", "afk": "AWAY", "quiet_thinking": "QUIET THINKING"}
CATEGORY_WORD = {"productive": "Productive", "neutral": "Neutral", "distraction": "Distraction"}
SOURCE_WORD = {"jev": "Jev", "cache": "Cached", "local": "Local model", "override": "Your override", "private": "Private surface"}
BANNER_SEC = 25
STALE_SEC = 6.0  # no engine update for this long marks the watcher as stale


def caps(text: str) -> QLabel:
    """Small condensed uppercase section label."""
    label = QLabel(text.upper())
    label.setFont(theme.caps_font(11))
    label.setStyleSheet(f"color: {theme.DIM};")
    return label


def dim(text: str = "", px: int = 12) -> QLabel:
    label = QLabel(text)
    label.setFont(theme.body_font(px))
    label.setStyleSheet(f"color: {theme.DIM};")
    label.setWordWrap(True)
    return label


class ElidedLabel(QLabel):
    """Single-line label that truncates with an ellipsis instead of widening its column."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(10)

    def paintEvent(self, _event: object) -> None:
        p = QPainter(self)
        p.setFont(self.font())
        p.setPen(self.palette().windowText().color())
        text = QFontMetrics(self.font()).elidedText(self.text(), Qt.TextElideMode.ElideRight, self.width())
        p.drawText(self.rect(), int(self.alignment()) | Qt.AlignmentFlag.AlignVCenter, text)


def mono_value(text: str = "") -> ElidedLabel:
    label = ElidedLabel(text)
    label.setFont(theme.mono_font(12))
    return label


def column(*, pad: tuple[int, int, int, int] = (16, 14, 16, 14)) -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("column")
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(*pad)
    lay.setSpacing(0)
    return frame, lay


class SensorChip(QWidget):
    """Header status: a dot plus 'NAME value'. Colour carries health; no animation."""

    def __init__(self, name: str) -> None:
        super().__init__()
        self._name, self._value, self._color = name, "...", theme.DIM
        self.setFixedHeight(22)

    def set(self, value: str, color: str) -> None:
        if (value, color) != (self._value, self._color):
            self._value, self._color = value, color
            self.updateGeometry()
            self.update()

    def sizeHint(self) -> QSize:
        name_w = QFontMetrics(theme.caps_font(11)).horizontalAdvance(self._name.upper())
        value_w = QFontMetrics(theme.body_font(12)).horizontalAdvance(self._value)
        return QSize(16 + name_w + 8 + value_w, 22)

    def paintEvent(self, _event: object) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(self._color))
        p.drawEllipse(QRectF(2, 8, 7, 7))
        p.setFont(theme.caps_font(11))
        p.setPen(QColor(theme.DIM))
        name_w = QFontMetrics(p.font()).horizontalAdvance(self._name.upper())
        p.drawText(QRectF(16, 0, name_w + 4, 22), Qt.AlignmentFlag.AlignVCenter, self._name.upper())
        p.setFont(theme.body_font(12))
        p.setPen(QColor(theme.INK))
        p.drawText(QRectF(16 + name_w + 8, 0, 400, 22), Qt.AlignmentFlag.AlignVCenter, self._value)


def fmt_age(sec: float) -> str:
    if sec < 10:
        return f"{sec:.1f} s"
    if sec < 120:
        return f"{sec:.0f} s"
    return f"{sec / 60:.0f} min"


class MainWindow(QMainWindow):
    """Everything the user sees. `apply(UiState)` is the single entry point for engine data."""

    def __init__(self, engine: EngineHandle) -> None:
        super().__init__()
        self._engine = engine
        self._state: UiState | None = None
        self._received = time.monotonic()
        self._last_nudge: str | None = None
        self.setWindowTitle("Focus Tracker")
        self.setWindowIcon(scan_eye_icon(theme.INK))
        self.resize(1180, 800)
        self.setMinimumSize(1020, 720)

        root = QFrame()
        root.setObjectName("root")
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.setCentralWidget(root)

        outer.addWidget(self._build_header())
        outer.addWidget(self._build_banner())
        columns = QFrame()
        columns.setObjectName("columns")
        grid = QHBoxLayout(columns)
        grid.setContentsMargins(0, 1, 0, 0)
        grid.setSpacing(1)
        grid.addWidget(self._build_camera_column(), 5)
        grid.addWidget(self._build_surface_column(), 5)
        grid.addWidget(self._build_side_column(), 3)
        outer.addWidget(columns, 1)
        outer.addWidget(self._build_traces())

        self._settings = SettingsDialog(self.command, self)
        self._ages = QTimer(self)
        self._ages.timeout.connect(self._refresh_ages)
        self._ages.start(1000)
        self._banner_timer = QTimer(self, singleShot=True, interval=BANNER_SEC * 1000)
        self._banner_timer.timeout.connect(self._banner.hide)

    def command(self, msg: dict[str, Any]) -> None:
        self._engine.command(msg)

    # ------------------------------------------------------------ building

    def _build_header(self) -> QWidget:
        bar = QFrame()
        bar.setStyleSheet("background: #08111B;")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(16, 7, 10, 7)
        lay.setSpacing(18)
        title = QLabel("FOCUS TRACKER")
        title.setFont(theme.display_font(14, condensed=True))
        lay.addWidget(title)
        lay.addStretch(1)
        self.chip_watcher = SensorChip("App watcher")
        self.chip_cam = SensorChip("Camera")
        self.chip_ext = SensorChip("Extension")
        self.chip_jev = SensorChip("Jev")
        for chip in (self.chip_watcher, self.chip_cam, self.chip_ext, self.chip_jev):
            lay.addWidget(chip)
        settings = QPushButton("Settings")
        settings.setObjectName("flat")
        settings.clicked.connect(lambda: self._settings.open_dialog())
        lay.addWidget(settings)
        return bar

    def _build_banner(self) -> QFrame:
        self._banner = QFrame()
        self._banner.setObjectName("banner")
        lay = QHBoxLayout(self._banner)
        lay.setContentsMargins(14, 8, 8, 8)
        self._banner_text = QLabel()
        self._banner_text.setFont(theme.body_font(14))
        self._banner_text.setStyleSheet(f"color: {theme.AMBER};")
        lay.addWidget(caps("Nudge"))
        lay.addWidget(self._banner_text, 1)
        close = QPushButton("Dismiss")
        close.setObjectName("flat")
        close.clicked.connect(self._banner.hide)
        lay.addWidget(close)
        self._banner.hide()
        return self._banner

    def _build_camera_column(self) -> QWidget:
        col, lay = column()
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.addWidget(caps("Camera · what MediaPipe sees"))
        head.addStretch(1)
        self.cam_picker = QComboBox()
        self.cam_picker.setFont(theme.body_font(11))
        self.cam_picker.setToolTip("Capture device")
        # `activated` fires only on user choice, so syncing from UiState never re-sends a command.
        self.cam_picker.activated.connect(self._on_camera_picked)
        self.cam_refresh = QPushButton("Refresh")
        self.cam_refresh.setToolTip("Look for newly connected cameras")
        self.cam_refresh.clicked.connect(self._refresh_cameras)
        head.addWidget(self.cam_picker)
        head.addWidget(self.cam_refresh)
        lay.addLayout(head)
        lay.addSpacing(8)
        self._cameras: list[tuple[int, str]] = []
        self._camera_index = 0
        self._refresh_cameras()
        self.camera = CameraView(self._engine.vision)
        self.camera.turn_on_clicked.connect(lambda: self.command({"cmd": "toggle_camera"}))
        lay.addWidget(self.camera)
        row = QHBoxLayout()
        row.setContentsMargins(0, 8, 0, 0)
        self.cam_presence = QLabel("Waiting")
        self.cam_presence.setFont(theme.body_font(12))
        self.cam_detail = QLabel("")
        self.cam_detail.setFont(theme.mono_font(12))
        self.cam_detail.setStyleSheet(f"color: {theme.DIM};")
        row.addWidget(self.cam_presence)
        row.addStretch(1)
        row.addWidget(self.cam_detail)
        lay.addLayout(row)
        readouts = QHBoxLayout()
        readouts.setSpacing(10)
        readouts.setContentsMargins(0, 10, 0, 0)
        self.rd_yaw, self.rd_pitch, self.rd_limit, self.rd_base = (QLabel("-") for _ in range(4))
        for name, value, px in (("Yaw", self.rd_yaw, 20), ("Pitch", self.rd_pitch, 20), ("Limit", self.rd_limit, 20), ("Baseline", self.rd_base, 13)):
            cell = QFrame()
            cell.setStyleSheet(f"QFrame {{ border-top: 1px solid {theme.LINE}; }} QLabel {{ border: none; }}")
            cl = QVBoxLayout(cell)
            cl.setContentsMargins(0, 6, 0, 0)
            cl.setSpacing(1)
            cl.addWidget(caps(name))
            value.setFont(theme.mono_font(px))
            value.setMinimumHeight(30)
            cl.addWidget(value)
            readouts.addWidget(cell, 2 if name == "Baseline" else 1)
        lay.addLayout(readouts)
        lay.addStretch(1)
        return col

    def _refresh_cameras(self) -> None:
        """Re-enumerate capture devices (cheap, does not open them) and rebuild the picker."""
        try:
            from focus_tracker.vision import list_cameras

            self._cameras = list_cameras()
        except ImportError:  # vision deps missing: picker shows only the configured index
            self._cameras = []
        self._fill_camera_picker()

    def _fill_camera_picker(self) -> None:
        self.cam_picker.clear()
        cams = list(self._cameras)
        if self._camera_index not in {i for i, _ in cams}:
            cams.insert(0, (self._camera_index, f"Camera {self._camera_index} (not found)"))
        for index, name in cams:
            self.cam_picker.addItem(name, index)
        self.cam_picker.setCurrentIndex(self.cam_picker.findData(self._camera_index))

    def _on_camera_picked(self, row: int) -> None:
        index = self.cam_picker.itemData(row)
        if isinstance(index, int) and index != self._camera_index:
            self._camera_index = index
            self.command({"cmd": "set_camera", "index": index})

    def _build_surface_column(self) -> QWidget:
        col, lay = column()
        lay.addWidget(caps("Foreground · app watcher + browser extension"))
        self.surface_big = ElidedLabel("Waiting for the engine")
        self.surface_big.setFont(theme.display_font(30))
        self.surface_big.setMinimumHeight(44)
        self.surface_sub = ElidedLabel("")
        self.surface_sub.setFont(theme.body_font(13))
        self.surface_sub.setStyleSheet(f"color: {theme.DIM};")
        lay.addSpacing(4)
        lay.addWidget(self.surface_big)
        lay.addWidget(self.surface_sub)
        lay.addSpacing(12)
        kv = QGridLayout()
        kv.setHorizontalSpacing(12)
        kv.setVerticalSpacing(6)
        kv.setColumnMinimumWidth(0, 96)
        kv.setColumnStretch(1, 1)
        self.kv: dict[str, ElidedLabel] = {}
        for row, name in enumerate(("Process", "Window", "URL path", "Input idle", "Extension", "On surface", "Classifier")):
            key = QLabel(name)
            key.setFont(theme.body_font(13))
            key.setStyleSheet(f"color: {theme.DIM};")
            val = mono_value() if name not in ("Extension", "Classifier") else ElidedLabel()
            if name in ("Extension", "Classifier"):
                val.setFont(theme.body_font(13))
            kv.addWidget(key, row, 0)
            kv.addWidget(val, row, 1)
            self.kv[name] = val
        lay.addLayout(kv)
        lay.addSpacing(16)
        lay.addWidget(self._build_verdict())
        lay.addStretch(1)
        return col

    def _build_verdict(self) -> QFrame:
        self.verdict = QFrame()
        self.verdict.setObjectName("verdict")
        lay = QVBoxLayout(self.verdict)
        lay.setContentsMargins(14, 10, 14, 12)
        lay.setSpacing(2)
        lay.addWidget(caps("Verdict against your goal"))
        self.verdict_word = QLabel("Unknown")
        self.verdict_word.setFont(theme.display_font(24))
        self.verdict_detail = ElidedLabel("")
        self.verdict_detail.setFont(theme.body_font(13))
        self.verdict_detail.setStyleSheet(f"color: {theme.DIM};")
        lay.addWidget(self.verdict_word)
        lay.addWidget(self.verdict_detail)
        lay.addSpacing(8)
        self.dwell_bar = QFrame()
        self.dwell_bar.setFixedHeight(6)
        self.dwell_bar.setStyleSheet(f"background: {theme.LINE};")
        self.dwell_fill = QFrame(self.dwell_bar)
        self.dwell_fill.setGeometry(0, 0, 0, 6)
        lay.addWidget(self.dwell_bar)
        self.dwell_text = dim("", 12)
        lay.addSpacing(2)
        lay.addWidget(self.dwell_text)
        return self.verdict

    def _build_side_column(self) -> QWidget:
        col, lay = column()
        lay.addWidget(caps("Goal"))
        lay.addSpacing(6)
        self.goal = QLineEdit()
        self.goal.setPlaceholderText("What are you working on?")
        self.goal.setFont(theme.body_font(14))
        self._goal_model = QStringListModel(self)
        completer = QCompleter(self._goal_model, self)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setCompletionMode(QCompleter.CompletionMode.UnfilteredPopupCompletion)
        self.goal.setCompleter(completer)
        self.goal.returnPressed.connect(self._submit_goal)
        completer.activated.connect(lambda _text: self._submit_goal())
        lay.addWidget(self.goal)
        lay.addSpacing(4)
        lay.addWidget(dim("Press Enter. Everything is judged against this."))
        lay.addSpacing(16)
        lay.addWidget(caps("State"))
        self.state_word = QLabel("-")
        self.state_word.setFont(theme.display_font(40, condensed=True))
        self.state_word.setMinimumHeight(52)
        lay.addWidget(self.state_word)
        self.state_sub = dim("")
        lay.addWidget(self.state_sub)
        lay.addSpacing(16)
        lay.addWidget(caps("Focus score"))
        self.score = QLabel("-")
        self.score.setFont(theme.mono_font(34))
        lay.addWidget(self.score)
        lay.addSpacing(10)
        btns = QGridLayout()
        btns.setSpacing(6)
        self.btn_calibrate = QPushButton("Calibrate")
        self.btn_camera = QPushButton("Camera off")
        self.btn_productive = QPushButton("This is productive")
        self.btn_snooze = QPushButton("Snooze 10 min")
        self.btn_exclude = QPushButton("Exclude surface")
        self.btn_calibrate.clicked.connect(lambda: self.command({"cmd": "calibrate"}))
        self.btn_camera.clicked.connect(lambda: self.command({"cmd": "toggle_camera"}))
        self.btn_productive.clicked.connect(lambda: self.command({"cmd": "reclassify", "category": "productive"}))
        self.btn_snooze.clicked.connect(lambda: self.command({"cmd": "snooze"}))
        self.btn_exclude.clicked.connect(lambda: self.command({"cmd": "exclude"}))
        for i, b in enumerate((self.btn_calibrate, self.btn_camera, self.btn_productive, self.btn_snooze)):
            btns.addWidget(b, i // 2, i % 2)
        btns.addWidget(self.btn_exclude, 2, 0, 1, 2)
        self.btn_productive.setObjectName("primary")
        lay.addLayout(btns)
        lay.addStretch(1)
        return col

    def _build_traces(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("traces")
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(6)
        lay.addWidget(caps("Last 25 minutes"))
        self.traces = TraceStrip()
        lay.addWidget(self.traces)
        return frame

    # ------------------------------------------------------------ updates

    def _submit_goal(self) -> None:
        text = self.goal.text().strip()
        if text:
            self.command({"cmd": "set_goal", "goal": text})
            self.goal.clearFocus()

    def apply(self, ui: UiState) -> None:
        """Render one engine tick."""
        self._state, self._received = ui, time.monotonic()
        t, extra = ui.tick, ui.extra
        yaw_max = ui.thresholds.get("yaw_max", 20.0)

        # Camera column
        self.camera.set_status(camera_on=ui.camera_on, degraded=t.camera.degraded)
        cam = t.camera
        if not ui.camera_on:
            self.cam_presence.setText("Camera off")
        elif cam.degraded:
            self.cam_presence.setText("Degraded")
        elif not cam.present:
            self.cam_presence.setText("No face detected")
        else:
            # Vision's own verdict (yaw and pitch box, flap guard); yaw-only fallback when it is absent.
            facing = extra.get("facing")
            if facing is None:
                facing = abs(cam.yaw) <= yaw_max
            self.cam_presence.setText("Present · " + ("facing screen" if facing else "looking away"))
        live = ui.camera_on and cam.present
        self.rd_yaw.setText(f"{cam.yaw:+.0f}°" if live else "-")
        self.rd_pitch.setText(f"{cam.pitch:+.0f}°" if live else "-")
        self.rd_limit.setText(f"±{yaw_max:.0f}°")
        self.rd_base.setText(str(extra.get("baseline") or "default"))
        self.btn_camera.setText("Camera off" if ui.camera_on else "Camera on")
        if ui.camera_index != self._camera_index:
            self._camera_index = ui.camera_index
            self._fill_camera_picker()

        # Foreground column
        s = ui.surface
        self.surface_big.setText(s.label if s else t.surface_label)
        if s:
            detail = s.page_title or s.window_title or ""
            self.surface_sub.setText(" · ".join(x for x in (detail, s.app if s.kind == "browser" else None) if x))
        self.kv["Process"].setText((ui.app.process if ui.app else None) or "-")
        self.kv["Window"].setText((ui.app.title if ui.app else None) or (s.window_title if s else None) or "-")
        self.kv["URL path"].setText(self._url_path(s.url if s else None))
        self._set_verdict(ui)
        self._refresh_ages()
        if ui.jev_online:
            lat = f" · {ui.jev_last_ms:.0f} ms" if ui.jev_last_ms is not None else ""
            self.kv["Classifier"].setText(f"Jev online{lat}")
        else:
            self.kv["Classifier"].setText("Jev offline, using local model")

        # Side column
        if not self.goal.hasFocus() and self.goal.text() != t.goal:
            self.goal.setText(t.goal)
            self.goal.setCursorPosition(0)
        self._goal_model.setStringList(ui.goal_history)
        color = theme.STATE_COLOR.get(t.state, theme.DIM)
        self.state_word.setText(STATE_WORD.get(t.state, t.state.upper()))
        self.state_word.setStyleSheet(f"color: {color};")
        focused_min = int(extra.get("focused_sec", 0)) // 60
        self.state_sub.setText(f"focused {focused_min} min this session")
        self.score.setText(str(t.pi))

        self._maybe_banner(extra.get("nudge_text"))
        self.traces.add(ui)
        self._settings.refresh(ui)

    @staticmethod
    def _url_path(url: str | None) -> str:
        if not url:
            return "-"
        return url.split("://", 1)[-1]

    def _set_verdict(self, ui: UiState) -> None:
        t, extra = ui.tick, ui.extra
        cat = t.category if t.category in theme.CATEGORY_COLOR else None
        color = theme.CATEGORY_COLOR.get(cat or "", theme.DIM)
        if t.classification_pending:
            word, detail = "Classifying", "waiting for Jev"
        elif cat is None:
            word = "Unknown"
            detail = "set a goal to start judging" if not t.goal else "no verdict yet"
        else:
            word = CATEGORY_WORD[cat]
            bits = [SOURCE_WORD.get(t.category_source or "", ""), f"confidence {t.confidence:.2f}" if t.confidence is not None else ""]
            detail = " · ".join(b for b in bits if b)
            if t.category_source == "jev" and ui.jev_last_ms is not None:
                detail += f" · {ui.jev_last_ms:.0f} ms"
        self.verdict_word.setText(word)
        self.verdict_word.setStyleSheet(f"color: {color};")
        self.verdict_detail.setText(detail)
        self.verdict.setStyleSheet(f"QFrame#verdict {{ border: 1px solid {theme.LINE}; border-left: 3px solid {color}; }} QLabel {{ border: none; }}")
        dwell, limit = float(extra.get("dwell_sec", 0)), float(extra.get("dwell_limit_sec", 60)) or 60.0
        frac = max(0.0, min(1.0, dwell / limit))
        self._dwell_frac, self._dwell_color = frac, color
        self.dwell_fill.setStyleSheet(f"background: {color};")
        self._layout_dwell()
        if cat == "distraction" and t.state in ("distracted", "nudged"):
            left = max(0.0, limit - dwell)
            self.dwell_text.setText(f"Nudge in {left:.0f} s if you stay and keep facing the screen")
        elif t.state == "afk":
            self.dwell_text.setText("Away from the desk, dwell clock paused")
        else:
            self.dwell_text.setText("Dwell clock idle, nothing to nudge about")
        # Remember when this surface's clock was read so the age row can tick.
        self._dwell_base = dwell

    _dwell_frac = 0.0
    _dwell_color = theme.DIM
    _dwell_base = 0.0

    def _layout_dwell(self) -> None:
        self.dwell_fill.setGeometry(0, 0, int(self.dwell_bar.width() * self._dwell_frac), 6)

    def resizeEvent(self, event: object) -> None:
        super().resizeEvent(event)  # type: ignore[arg-type]
        self._layout_dwell()

    def _refresh_ages(self) -> None:
        """1 Hz: tick the 'x s ago' readouts between engine updates so liveness is visible."""
        ui = self._state
        if ui is None:
            return
        elapsed = time.monotonic() - self._received
        now_ms = time.time() * 1000
        idle = (ui.app.input_idle_sec if ui.app else float(ui.extra.get("input_idle_sec", 0))) + elapsed
        self.kv["Input idle"].setText(fmt_age(idle) if idle >= 1 else "active")
        on_surface = float(ui.extra.get("dwell_sec", 0)) + (elapsed if ui.tick.state in ("distracted", "nudged") else 0)
        self.kv["On surface"].setText(fmt_age(on_surface))
        if not ui.browser_connected:
            ext, ext_color = "not connected", theme.DIST
        elif ui.browser_last_event_ms is None:
            ext, ext_color = "connected, no tab events yet", theme.PROD
        else:
            age = max(0.0, (now_ms - ui.browser_last_event_ms) / 1000)
            ext, ext_color = f"connected · last tab event {fmt_age(age)} ago", theme.PROD
        self.kv["Extension"].setText(ext)
        self.kv["Extension"].setStyleSheet(f"color: {theme.INK if ui.browser_connected else theme.DIST};")

        stale = elapsed > STALE_SEC
        watcher_age = max(0.0, now_ms - ui.app.ts) / 1000 if ui.app and ui.app.ts else None
        self.chip_watcher.set(
            "no signal" if ui.app is None else f"{ui.app.process or 'ok'} · {fmt_age(watcher_age) if watcher_age is not None else 'live'} ago",
            theme.DIST if stale or ui.app is None else theme.PROD,
        )
        if not ui.camera_on:
            self.chip_cam.set("off", theme.DIM)
        elif ui.tick.camera.degraded:
            self.chip_cam.set("degraded", theme.AMBER)
        else:
            self.chip_cam.set(f"{self.camera.fps:.1f} fps · {self.camera.n_points} pts", theme.PROD)
        self.chip_ext.set("connected" if ui.browser_connected else "offline", ext_color if ui.browser_connected else theme.DIST)
        self.chip_jev.set(
            f"{ui.jev_last_ms:.0f} ms" if ui.jev_online and ui.jev_last_ms is not None else ("online" if ui.jev_online else "offline"),
            theme.PROD if ui.jev_online else theme.DIST,
        )
        self.cam_detail.setText(f"{self.camera.fps:.1f} fps · {self.camera.n_points} pts" if ui.camera_on else "")

    def _maybe_banner(self, text: object) -> None:
        if isinstance(text, str) and text and text != self._last_nudge:
            self._banner_text.setText(text)
            self._banner.show()
            self._banner_timer.start()
        self._last_nudge = text if isinstance(text, str) else None

    # ------------------------------------------------------------ lifecycle

    quitting = False
    on_close_to_tray: Callable[[], bool] | None = None

    def closeEvent(self, event: QCloseEvent) -> None:
        """Hide to the tray when one exists; otherwise a normal close quits."""
        if not self.quitting and self.on_close_to_tray is not None and self.on_close_to_tray():
            event.ignore()
            self.hide()
            return
        super().closeEvent(event)
