"""Tray and window icon: the Lucide "scan-eye" glyph rendered through QtSvg."""

from __future__ import annotations

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from focus_tracker.ui import theme

# Lucide "scan-eye" (https://lucide.dev, ISC license). 24x24 viewBox, stroke based.
SCAN_EYE_PATHS = (
    '<path d="M3 7V5a2 2 0 0 1 2-2h2"/>'
    '<path d="M17 3h2a2 2 0 0 1 2 2v2"/>'
    '<path d="M21 17v2a2 2 0 0 1-2 2h-2"/>'
    '<path d="M7 21H5a2 2 0 0 1-2-2v-2"/>'
    '<circle cx="12" cy="12" r="1"/>'
    '<path d="M18.944 12.33a1 1 0 0 0 0-.66 7.5 7.5 0 0 0-13.888 0 1 1 0 0 0 0 .66 7.5 7.5 0 0 0 13.888 0"/>'
)


def scan_eye_icon(color: str = theme.INK, size: int = 64) -> QIcon:
    """The glyph stroked in `color`, on a transparent background."""
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        f'stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        f"{SCAN_EYE_PATHS}</svg>"
    )
    renderer = QSvgRenderer(QByteArray(svg.encode()))
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    renderer.render(painter)
    painter.end()
    return QIcon(pix)


def state_icon(state: str | None) -> QIcon:
    """Icon tinted by engine state: focused green, distracted/nudged red, anything else dim."""
    tint = {"focused": theme.PROD, "distracted": theme.DIST, "nudged": theme.DIST}.get(state or "", theme.DIM)
    return scan_eye_icon(tint)
