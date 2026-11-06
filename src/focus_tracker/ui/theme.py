"""Polygraph palette, fonts and the global stylesheet."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPen

BG = "#0B1622"
PANEL = "#0F1E2E"
LINE = "#1B3047"
INK = "#DCE6F0"
DIM = "#7F93A8"
APP = "#7FB7FF"
APP_ALT = "#5E95D9"
FACE = "#F2C14E"
PROD = "#5FD0B0"
NEU = "#8C9BB0"
DIST = "#FF7A6B"
AMBER = "#F2C14E"
CAM_BG = "#060D14"

CATEGORY_COLOR = {"productive": PROD, "neutral": NEU, "distraction": DIST}
STATE_COLOR = {"focused": PROD, "distracted": DIST, "nudged": DIST, "quiet_thinking": DIM, "afk": DIM}
# Fill behind a state block in the trace strip.
STATE_FILL = {"focused": "#16324A", "distracted": "#3A2226", "nudged": "#3A2226", "quiet_thinking": "#1A2633", "afk": "#1A2633"}


def color(hex_: str, alpha: int = 255) -> QColor:
    c = QColor(hex_)
    c.setAlpha(alpha)
    return c


# Windows families come first in every stack; these Linux fallbacks only apply when none are installed.
_LABEL_LINUX = ["Barlow Semi Condensed", "Archivo Narrow", "DejaVu Sans Condensed"]
_LABEL_FAMILIES = ["Bahnschrift SemiCondensed", "Bahnschrift", *_LABEL_LINUX]
_BODY_LINUX = ["Inter", "Noto Sans", "Cantarell", "DejaVu Sans"]


def caps_font(px: int = 11) -> QFont:
    """Condensed uppercase label face (Bahnschrift on Windows)."""
    f = QFont()
    f.setFamilies([*_LABEL_FAMILIES, "Segoe UI", *_BODY_LINUX])
    f.setPixelSize(px)
    f.setWeight(QFont.Weight.DemiBold)
    f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.9)
    return f


def display_font(px: int, *, condensed: bool = False) -> QFont:
    f = QFont()
    f.setFamilies(
        ["Bahnschrift Condensed", "Bahnschrift"] if condensed else ["Bahnschrift SemiCondensed", "Bahnschrift"]
    )
    f.setFamilies([*f.families(), *_LABEL_LINUX, "Segoe UI", *_BODY_LINUX])
    f.setPixelSize(px)
    f.setWeight(QFont.Weight.Bold if condensed else QFont.Weight.DemiBold)
    return f


def mono_font(px: int) -> QFont:
    f = QFont()
    f.setFamilies(["Cascadia Mono", "Cascadia Code", "Consolas", "JetBrains Mono", "Fira Mono", "DejaVu Sans Mono"])
    f.setStyleHint(QFont.StyleHint.Monospace)
    f.setPixelSize(px)
    return f


def body_font(px: int = 13) -> QFont:
    f = QFont()
    f.setFamilies(["Segoe UI Variable Text", "Segoe UI", "Arial", *_BODY_LINUX])
    f.setPixelSize(px)
    return f


STYLESHEET = f"""
QWidget {{ color: {INK}; }}
QMainWindow, QDialog, #root {{ background: {BG}; }}
#column {{ background: {BG}; }}
#columns {{ background: {LINE}; }}
#traces {{ background: {BG}; border-top: 1px solid {LINE}; }}
QToolTip {{ background: {PANEL}; color: {INK}; border: 1px solid {LINE}; }}
QPushButton {{
    background: {PANEL}; color: {INK}; border: 1px solid {LINE};
    padding: 6px 11px; border-radius: 3px;
}}
QPushButton:hover {{ border-color: {APP_ALT}; }}
QPushButton:pressed {{ background: {LINE}; }}
QPushButton:disabled {{ color: {DIM}; }}
QPushButton#primary {{ border-color: {APP_ALT}; }}
QPushButton#flat {{ background: transparent; border: none; color: {DIM}; padding: 4px 8px; }}
QPushButton#flat:hover {{ color: {INK}; }}
QLineEdit {{
    background: {PANEL}; border: 1px solid {LINE}; border-radius: 3px;
    padding: 7px 10px; selection-background-color: {APP_ALT}; selection-color: {BG};
}}
QLineEdit:focus {{ border-color: {APP_ALT}; }}
QFrame#banner {{ background: #2A2314; border: 1px solid {AMBER}; border-left: 3px solid {AMBER}; }}
QFrame#verdict {{ border: 1px solid {LINE}; border-left: 3px solid {NEU}; }}
QAbstractItemView {{
    background: {PANEL}; color: {INK}; border: 1px solid {LINE};
    selection-background-color: {LINE}; selection-color: {INK};
}}
QHeaderView::section {{ background: {BG}; color: {DIM}; border: none; border-bottom: 1px solid {LINE}; padding: 4px 6px; }}
QTableWidget {{ gridline-color: {LINE}; }}
QSlider::groove:horizontal {{ height: 4px; background: {LINE}; }}
QSlider::sub-page:horizontal {{ background: {APP_ALT}; }}
QSlider::handle:horizontal {{ width: 12px; margin: -5px 0; border-radius: 6px; background: {APP}; }}
QComboBox {{
    background: {PANEL}; color: {INK}; border: 1px solid {LINE};
    padding: 3px 8px; border-radius: 3px; min-width: 150px;
}}
QComboBox::drop-down {{ border: 0; width: 18px; }}
QComboBox QAbstractItemView {{
    background: {PANEL}; color: {INK}; border: 1px solid {LINE};
    selection-background-color: {LINE}; outline: 0;
}}
QMenu {{ background: {PANEL}; color: {INK}; border: 1px solid {LINE}; padding: 4px; }}
QMenu::item {{ padding: 6px 22px; }}
QMenu::item:selected {{ background: {LINE}; }}
"""


def pen(col: QColor | str, width: float, cap: Qt.PenCapStyle = Qt.PenCapStyle.RoundCap) -> QPen:
    """Solid round-joined pen (PySide's QPen has no keyword arguments)."""
    return QPen(QColor(col), width, Qt.PenStyle.SolidLine, cap, Qt.PenJoinStyle.RoundJoin)
