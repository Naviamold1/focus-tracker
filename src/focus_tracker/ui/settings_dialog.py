"""Settings dialog: threshold sliders, classifier cache table, demo triggers."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from focus_tracker.schema import UiState
from focus_tracker.ui import theme

# name -> (label, lower, upper, step). Mirrors the engine's THRESHOLD_BOUNDS.
SLIDERS = {
    "dwell_sec": ("Dwell before nudge (s)", 30.0, 120.0, 5.0),
    "afk_sec": ("Away after idle (s)", 60.0, 600.0, 10.0),
    "trust_conf": ("Trust confidence", 0.5, 1.0, 0.05),
    "yaw_max": ("Facing limit (deg)", 10.0, 35.0, 1.0),
}
CACHE_COLUMNS = ("Key / label", "Category", "Source", "Confidence", "Age")


class SettingsDialog(QDialog):
    """Non-modal. Sends set_threshold and demo commands through `send`."""

    def __init__(self, send: Callable[[dict[str, Any]], None], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._send = send
        self.setWindowTitle("Settings and cache")
        self.resize(640, 560)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(8)

        self._sliders: dict[str, QSlider] = {}
        self._values: dict[str, QLabel] = {}
        head = QLabel("THRESHOLDS")
        head.setFont(theme.caps_font(11))
        head.setStyleSheet(f"color: {theme.DIM};")
        lay.addWidget(head)
        for name, (label, lo, hi, step) in SLIDERS.items():
            row = QHBoxLayout()
            title = QLabel(label)
            title.setMinimumWidth(170)
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(0, round((hi - lo) / step))
            value = QLabel("-")
            value.setFont(theme.mono_font(12))
            value.setMinimumWidth(48)
            value.setAlignment(Qt.AlignmentFlag.AlignRight)
            slider.valueChanged.connect(lambda v, n=name: self._on_slider(n, v))
            slider.sliderReleased.connect(lambda n=name: self._commit(n))
            row.addWidget(title)
            row.addWidget(slider, 1)
            row.addWidget(value)
            lay.addLayout(row)
            self._sliders[name], self._values[name] = slider, value

        lay.addSpacing(8)
        cache_head = QLabel("CLASSIFIER CACHE")
        cache_head.setFont(theme.caps_font(11))
        cache_head.setStyleSheet(f"color: {theme.DIM};")
        lay.addWidget(cache_head)
        self.table = QTableWidget(0, len(CACHE_COLUMNS))
        self.table.setHorizontalHeaderLabels(CACHE_COLUMNS)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.horizontalHeader().setSectionResizeMode(0, self.table.horizontalHeader().ResizeMode.Stretch)
        lay.addWidget(self.table, 1)

        demo = QHBoxLayout()
        demo_label = QLabel("Demo")
        demo_label.setStyleSheet(f"color: {theme.DIM};")
        demo.addWidget(demo_label)
        for letter, tip in (("D", "Distraction scene"), ("G", "Goal flip scene"), ("R", "Reset")):
            b = QPushButton(letter)
            b.setToolTip(tip)
            b.setFixedWidth(36)
            b.clicked.connect(lambda _=False, w=letter: self._send({"cmd": "demo", "which": w}))
            demo.addWidget(b)
        demo.addStretch(1)
        lay.addLayout(demo)

    def open_dialog(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()

    def _to_value(self, name: str, pos: int) -> float:
        _, lo, _, step = SLIDERS[name]
        return round(lo + pos * step, 4)

    def _on_slider(self, name: str, pos: int) -> None:
        value = self._to_value(name, pos)
        self._values[name].setText(f"{value:g}")
        if not self._sliders[name].isSliderDown() and self.isVisible() and not self._sliders[name].signalsBlocked():
            self._commit(name)  # keyboard / wheel adjustments

    def _commit(self, name: str) -> None:
        self._send({"cmd": "set_threshold", "name": name, "value": self._to_value(name, self._sliders[name].value())})

    def refresh(self, ui: UiState) -> None:
        """Mirror engine thresholds and the cache rows (no-op while hidden, to stay cheap)."""
        if not self.isVisible():
            return
        for name, slider in self._sliders.items():
            if name in ui.thresholds and not slider.isSliderDown():
                _, lo, _, step = SLIDERS[name]
                slider.blockSignals(True)
                slider.setValue(round((ui.thresholds[name] - lo) / step))
                slider.blockSignals(False)
                self._values[name].setText(f"{ui.thresholds[name]:g}")
        now = time.time() * 1000
        self.table.setRowCount(len(ui.cache))
        for r, row in enumerate(ui.cache):
            conf = row.get("confidence")
            ts = row.get("ts")
            cells = (
                str(row.get("label") or row.get("key") or ""),
                str(row.get("category") or ""),
                str(row.get("source") or ""),
                f"{conf:.2f}" if isinstance(conf, int | float) else "",
                f"{(now - ts) / 60000:.0f} min" if isinstance(ts, int | float) and ts else "",
            )
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if c == 1 and text in theme.CATEGORY_COLOR:
                    item.setForeground(theme.color(theme.CATEGORY_COLOR[text]))
                self.table.setItem(r, c, item)
