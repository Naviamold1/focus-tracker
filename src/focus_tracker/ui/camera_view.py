"""Live camera panel: the real frame with the detector's overlay painted on top."""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFontMetrics,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
)
from PySide6.QtWidgets import QPushButton, QWidget

from focus_tracker.schema import FrameSnapshot, VisionLike
from focus_tracker.ui import theme
from focus_tracker.ui.frames import CLOSED_LOOPS, FACE_OVAL, OPEN_PATHS, frame_to_qimage

POLL_MS = 100  # ~10 Hz; repaints only when the snapshot timestamp changes
AXIS_COLORS = ("#FF5A5A", "#5CE08A", "#5C9DFF")  # x red, y green, z blue

type ToView = Callable[[float, float], QPointF]


class CameraView(QWidget):
    """Polls `get_vision().latest_frame()` and paints frame + landmarks + box + pose axes + HUD."""

    turn_on_clicked = Signal()

    def __init__(self, get_vision: Callable[[], VisionLike | None], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._get_vision = get_vision
        self._snap: FrameSnapshot | None = None
        self._image: QImage | None = None
        self._ts = -1
        self.fps = 0.0
        self.camera_on = True
        self.degraded = False
        self.setMinimumSize(360, 250)
        self._button = QPushButton("Turn on", self)
        self._button.setObjectName("primary")
        self._button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._button.clicked.connect(self.turn_on_clicked)
        self._button.hide()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.poll)
        self._timer.start(POLL_MS)

    @property
    def n_points(self) -> int:
        lm = self._snap.landmarks if self._snap else None
        return 0 if lm is None else len(lm)

    def set_status(self, *, camera_on: bool, degraded: bool) -> None:
        if (camera_on, degraded) != (self.camera_on, self.degraded):
            self.camera_on, self.degraded = camera_on, degraded
            self._button.setVisible(not camera_on)
            self.update()

    def poll(self) -> None:
        """Pull the newest frame; repaint only when it changed."""
        vision = self._get_vision()
        snap = vision.latest_frame() if vision is not None and self.camera_on else None
        if vision is not None and (ev := vision.latest()) is not None:
            self.fps = ev.fps
        if snap is None:
            if self._snap is not None:
                self._snap, self._image, self._ts = None, None, -1
                self.update()
            return
        if snap.ts != self._ts:
            self._snap, self._ts = snap, snap.ts
            self._image = frame_to_qimage(snap.image)
            self.update()

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, w: int) -> int:
        return w * 3 // 4

    def resizeEvent(self, _event: object) -> None:
        self._button.adjustSize()
        self._button.move((self.width() - self._button.width()) // 2, self.height() // 2 + 26)

    # ------------------------------------------------------------ painting

    def paintEvent(self, _event: object) -> None:
        p = QPainter(self)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        outer = QRectF(self.rect())
        p.fillRect(outer, QColor(theme.CAM_BG))
        if not self.camera_on:
            self._centered(p, outer, "Camera off", "No frames are captured and the device is released.")
        elif self._image is None or self._snap is None:
            self._centered(p, outer, "Waiting for camera", "Opening the device and loading the face model.")
        else:
            self._paint_frame(p, outer, self._image, self._snap)
        if self.camera_on and self.degraded:
            self._note(p, outer, "Camera degraded. Head pose is ignored until frames recover.")
        p.setPen(QPen(QColor(theme.LINE), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(outer.adjusted(0.5, 0.5, -0.5, -0.5))

    def _paint_frame(self, p: QPainter, outer: QRectF, img: QImage, snap: FrameSnapshot) -> None:
        scale = min(outer.width() / img.width(), outer.height() / img.height())
        w, h = img.width() * scale, img.height() * scale
        target = QRectF(outer.center().x() - w / 2, outer.center().y() - h / 2, w, h)
        p.drawImage(target, img)

        def to_view(x: float, y: float) -> QPointF:
            return QPointF(target.left() + x * scale, target.top() + y * scale)

        p.save()
        p.setClipRect(target)
        if snap.landmarks is not None:
            self._paint_mesh(p, snap, to_view)
        else:
            self._chip(p, QPointF(target.center().x(), target.bottom() - 30), "no face detected", theme.AMBER, center=True)
        self._paint_hud(p, target, img)
        p.restore()

    def _paint_mesh(self, p: QPainter, snap: FrameSnapshot, to_view: ToView) -> None:
        lm = snap.landmarks
        assert lm is not None
        # All landmark dots: small and translucent so the contours stay readable.
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.color(theme.APP, 115))
        for x, y in lm:
            p.drawEllipse(to_view(float(x), float(y)), 1.3, 1.3)
        p.setBrush(Qt.BrushStyle.NoBrush)
        for loop in CLOSED_LOOPS:
            self._poly(p, lm, loop, to_view, closed=True)
        for path in OPEN_PATHS:
            self._poly(p, lm, path, to_view, closed=False)
        if snap.box is not None:
            self._paint_box(p, snap, to_view)
        if snap.nose is not None and snap.axes is not None:
            origin = to_view(*snap.nose)
            for end, col in zip(snap.axes, AXIS_COLORS, strict=True):
                p.setPen(theme.pen(theme.color("#000000", 150), 4.5))
                p.drawLine(origin, to_view(*end))
                p.setPen(theme.pen(col, 2.2))
                p.drawLine(origin, to_view(*end))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(theme.INK))
            p.drawEllipse(origin, 2.6, 2.6)

    def _poly(self, p: QPainter, lm: np.ndarray, idx: tuple[int, ...], to_view: ToView, *, closed: bool) -> None:
        pts = [to_view(float(lm[i][0]), float(lm[i][1])) for i in idx if i < len(lm)]
        col = theme.APP if idx == FACE_OVAL else theme.FACE
        p.setPen(theme.pen(theme.color(col, 215), 1.4))
        poly = QPolygonF(pts)
        p.drawPolygon(poly) if closed else p.drawPolyline(poly)

    def _paint_box(self, p: QPainter, snap: FrameSnapshot, to_view: ToView) -> None:
        """Corner brackets around the detected face plus its score chip."""
        assert snap.box is not None
        x, y, w, h = snap.box
        a, b = to_view(x, y), to_view(x + w, y + h)
        arm = min(b.x() - a.x(), b.y() - a.y()) * 0.2
        p.setPen(theme.pen(theme.FACE, 2, Qt.PenCapStyle.SquareCap))
        path = QPainterPath()
        for cx, cy, dx, dy in (
            (a.x(), a.y(), 1, 1),
            (b.x(), a.y(), -1, 1),
            (b.x(), b.y(), -1, -1),
            (a.x(), b.y(), 1, -1),
        ):
            path.moveTo(cx + dx * arm, cy)
            path.lineTo(cx, cy)
            path.lineTo(cx, cy + dy * arm)
        p.drawPath(path)
        # MediaPipe Face Landmarker gives no per-face score, so the real engine sends None.
        label = "face" if snap.face_score is None else f"face {snap.face_score:.2f}"
        self._chip(p, QPointF(a.x() + 1, a.y() + 3), label, theme.FACE, filled=True)

    def _paint_hud(self, p: QPainter, target: QRectF, img: QImage) -> None:
        text = f"LIVE  cam {img.width()}x{img.height()}  ·  {self.fps:.1f} fps"
        p.setFont(theme.mono_font(11))
        fm = QFontMetrics(p.font())
        box = QRectF(target.left() + 8, target.top() + 8, fm.horizontalAdvance(text) + 26, 22)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.color("#04101A", 190))
        p.drawRoundedRect(box, 3, 3)
        p.setBrush(QColor(theme.PROD))
        p.drawEllipse(QPointF(box.left() + 10, box.center().y()), 3.2, 3.2)
        p.setPen(QColor(theme.INK))
        p.drawText(box.adjusted(20, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)

    def _chip(
        self,
        p: QPainter,
        at: QPointF,
        text: str,
        col: str,
        *,
        filled: bool = False,
        above: bool = False,
        center: bool = False,
    ) -> None:
        """Small mono label anchored at `at` (bottom-left if `above`, horizontally centered if `center`)."""
        p.save()
        p.setFont(theme.mono_font(11))
        fm = QFontMetrics(p.font())
        w, h = fm.horizontalAdvance(text) + 14, 18
        x = at.x() - w / 2 if center else at.x()
        y = at.y() - h if above else at.y()
        r = QRectF(x, y, w, h)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(col) if filled else theme.color("#04101A", 200))
        p.drawRoundedRect(r, 2, 2)
        p.setPen(QColor(theme.BG) if filled else QColor(col))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, text)
        p.restore()

    def _centered(self, p: QPainter, r: QRectF, title: str, sub: str) -> None:
        cy = r.center().y()
        p.setPen(QColor(theme.INK))
        p.setFont(theme.display_font(22))
        p.drawText(QRectF(r.left(), cy - 40, r.width(), 30), Qt.AlignmentFlag.AlignCenter, title)
        p.setPen(QColor(theme.DIM))
        p.setFont(theme.body_font(12))
        p.drawText(QRectF(r.left() + 12, cy - 6, r.width() - 24, 20), Qt.AlignmentFlag.AlignCenter, sub)

    def _note(self, p: QPainter, r: QRectF, text: str) -> None:
        p.setFont(theme.body_font(12))
        box = QRectF(r.left() + 8, r.bottom() - 32, r.width() - 16, 24)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.color("#2A2314", 235))
        p.drawRoundedRect(box, 3, 3)
        p.setPen(QColor(theme.AMBER))
        p.drawText(box.adjusted(10, 0, -10, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)
