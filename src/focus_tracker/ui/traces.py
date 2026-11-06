"""The "Last 25 minutes" polygraph: five lanes on one shared clock."""

from __future__ import annotations

import itertools
import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QWidget

from focus_tracker.schema import State, UiState
from focus_tracker.ui import theme

WINDOW_MS = 25 * 60 * 1000
TICK_MS = 2000  # the engine publishes every 2 s; the newest sample is drawn this wide
MAX_GAP_MS = 8000  # a sample never stretches over a longer hole than this
YAW_FULL_SCALE = 45.0  # degrees at the lane edge
LANES = ("APP", "FACE", "YAW", "VERDICT", "STATE")
GUTTER = 70


@dataclass(frozen=True, slots=True)
class Sample:
    """One engine tick, reduced to what the strip draws."""

    ts: int  # epoch ms
    app: str
    cam_on: bool
    present: bool
    yaw: float
    yaw_limit: float
    category: str | None
    source: str | None
    confidence: float | None
    state: State

    @classmethod
    def from_ui(cls, ui: UiState) -> Sample:
        t = ui.tick
        return cls(
            ts=t.ts or int(time.time() * 1000),
            app=t.surface_label,
            cam_on=ui.camera_on,
            present=t.camera.present,
            yaw=t.camera.yaw,
            yaw_limit=ui.thresholds.get("yaw_max", 20.0),
            category=t.category if t.category in theme.CATEGORY_COLOR else None,
            source=t.category_source,
            confidence=t.confidence,
            state=t.state,
        )


class TraceHistory:
    """Rolling per-tick history, trimmed to WINDOW_MS behind the newest sample."""

    def __init__(self) -> None:
        self._samples: deque[Sample] = deque()

    def __len__(self) -> int:
        return len(self._samples)

    @property
    def samples(self) -> list[Sample]:
        return list(self._samples)

    def add(self, s: Sample) -> None:
        if self._samples and s.ts <= self._samples[-1].ts:
            return  # duplicate publish (e.g. after a command)
        self._samples.append(s)
        while self._samples[0].ts < s.ts - WINDOW_MS:
            self._samples.popleft()


def runs[K](samples: Sequence[Sample], key: Callable[[Sample], K]) -> list[tuple[int, int, Sample]]:
    """Merge consecutive samples with the same key into (start_ms, end_ms, first_sample)."""
    out: list[tuple[int, int, Sample]] = []
    for i, s in enumerate(samples):
        end = min(samples[i + 1].ts, s.ts + MAX_GAP_MS) if i + 1 < len(samples) else s.ts + TICK_MS
        if out and out[-1][1] == s.ts and key(out[-1][2]) == key(s):
            out[-1] = (out[-1][0], end, out[-1][2])
        else:
            out.append((s.ts, end, s))
    return out


class TraceStrip(QWidget):
    """Custom-painted lanes: APP, FACE, YAW (with limit band), VERDICT, STATE, plus a time axis."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.history = TraceHistory()
        self.setMinimumHeight(196)

    def add(self, ui: UiState) -> None:
        self.history.add(Sample.from_ui(ui))
        self.update()

    # ------------------------------------------------------------ painting

    def paintEvent(self, _event: object) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        samples = self.history.samples
        axis_h = 20
        lane_h = (self.height() - axis_h) / len(LANES)
        left, right = GUTTER, self.width() - 2
        p.setFont(theme.caps_font(11))
        for i, name in enumerate(LANES):
            p.setPen(QColor(theme.DIM))
            p.drawText(QRectF(0, i * lane_h, left - 8, lane_h), Qt.AlignmentFlag.AlignVCenter, name)
            if i:
                p.setPen(QPen(QColor(theme.LINE), 1))
                y = round(i * lane_h) + 0.5
                p.drawLine(QPointF(left, y), QPointF(right, y))
        if not samples:
            p.setPen(QColor(theme.DIM))
            p.setFont(theme.body_font(12))
            p.drawText(QRectF(left, 0, right - left, self.height() - axis_h), Qt.AlignmentFlag.AlignCenter, "Collecting history...")
            return

        end_ms = samples[-1].ts + TICK_MS
        start_ms = end_ms - WINDOW_MS
        span = right - left

        def x_of(t: int) -> float:
            return left + (t - start_ms) / WINDOW_MS * span

        def lane(i: int) -> QRectF:
            return QRectF(left, i * lane_h, span, lane_h)

        self._draw_axis(p, start_ms, end_ms, x_of, lane_h * len(LANES), left, right)
        self._draw_app(p, samples, lane(0), x_of)
        self._draw_face(p, samples, lane(1), x_of)
        self._draw_yaw(p, samples, lane(2), x_of)
        self._draw_verdict(p, samples, lane(3), x_of)
        self._draw_state(p, samples, lane(4), x_of)

    def _blocks(
        self,
        p: QPainter,
        rect: QRectF,
        x_of: Callable[[int], float],
        items: list[tuple[int, int, str, str, str, float]],
        *,
        pad: float = 0.22,
    ) -> None:
        """Draw (start, end, fill, label, label_color, alpha) blocks, labelled only where the text fits."""
        p.setFont(theme.body_font(11))
        fm = QFontMetrics(p.font())
        top, h = rect.top() + rect.height() * pad, rect.height() * (1 - 2 * pad)
        for a, b, fill, label, label_color, alpha in items:
            x0, x1 = max(x_of(a), rect.left()), min(x_of(b), rect.right())
            if x1 <= x0:
                continue
            r = QRectF(x0, top, max(x1 - x0 - 1, 1.5), h)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.color(fill, int(alpha * 255)))
            p.drawRect(r)
            if label and fm.horizontalAdvance(label) + 14 <= r.width():
                p.setPen(QColor(label_color))
                p.drawText(r.adjusted(7, 0, -4, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, label)

    def _draw_app(self, p: QPainter, samples: Sequence[Sample], rect: QRectF, x_of: Callable[[int], float]) -> None:
        shades: dict[str, str] = {}
        items = []
        for a, b, s in runs(samples, lambda s: s.app):
            shade = shades.setdefault(s.app, (theme.APP, theme.APP_ALT)[len(shades) % 2])
            items.append((a, b, shade, s.app, theme.BG, 1.0))
        self._blocks(p, rect, x_of, items)

    def _draw_face(self, p: QPainter, samples: Sequence[Sample], rect: QRectF, x_of: Callable[[int], float]) -> None:
        items = []
        for a, b, s in runs(samples, lambda s: (s.cam_on, s.present)):
            if not s.cam_on:
                items.append((a, b, theme.LINE, "camera off", theme.DIM, 0.6))
            elif s.present:
                items.append((a, b, theme.FACE, "", theme.BG, 1.0))
            else:
                items.append((a, b, "#4A4026", "away", theme.DIM, 1.0))
        self._blocks(p, rect, x_of, items, pad=0.22)

    def _draw_yaw(self, p: QPainter, samples: Sequence[Sample], rect: QRectF, x_of: Callable[[int], float]) -> None:
        mid, half = rect.center().y(), rect.height() * 0.42

        def y_of(deg: float) -> float:
            return mid - max(-1.0, min(1.0, deg / YAW_FULL_SCALE)) * half

        limit = samples[-1].yaw_limit
        band = QRectF(rect.left(), y_of(limit), rect.width(), y_of(-limit) - y_of(limit))
        p.fillRect(band, QColor("#122538"))
        p.setPen(QPen(theme.color(theme.DIM, 60), 1, Qt.PenStyle.DashLine))
        p.drawLine(QPointF(rect.left(), mid), QPointF(rect.right(), mid))
        p.setPen(QColor(theme.DIM))
        p.setFont(theme.mono_font(10))
        p.drawText(rect.adjusted(0, 0, -6, -2), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom, f"±{limit:.0f}° band")
        # Segment by segment so out-of-band stretches turn red and absent samples break the line.
        for prev, cur in itertools.pairwise(samples):
            if not (prev.present and cur.present) or cur.ts - prev.ts > MAX_GAP_MS:
                continue
            outside = abs(cur.yaw) > cur.yaw_limit
            p.setPen(QPen(QColor(theme.DIST if outside else theme.INK), 1.5))
            p.drawLine(QPointF(x_of(prev.ts), y_of(prev.yaw)), QPointF(x_of(cur.ts), y_of(cur.yaw)))

    def _draw_verdict(self, p: QPainter, samples: Sequence[Sample], rect: QRectF, x_of: Callable[[int], float]) -> None:
        items = []
        for a, b, s in runs(samples, lambda s: (s.category, s.source)):
            fill = theme.CATEGORY_COLOR.get(s.category or "", theme.LINE)
            detail = ""
            if s.source and s.confidence is not None:
                detail = f"{s.source} {s.confidence:.2f}"
            elif s.source:
                detail = s.source
            alpha = 1.0 if s.source in ("jev", "override") else 0.55
            items.append((a, b, fill, detail, theme.BG, alpha if s.category else 0.5))
        self._blocks(p, rect, x_of, items)

    def _draw_state(self, p: QPainter, samples: Sequence[Sample], rect: QRectF, x_of: Callable[[int], float]) -> None:
        items = [
            (a, b, theme.STATE_FILL[s.state], s.state.replace("_", " "), theme.STATE_COLOR[s.state] if s.state != "focused" else theme.INK, 1.0)
            for a, b, s in runs(samples, lambda s: s.state)
        ]
        self._blocks(p, rect, x_of, items)

    def _draw_axis(
        self, p: QPainter, start_ms: int, end_ms: int, x_of: Callable[[int], float], top: float, left: float, right: float
    ) -> None:
        step = 5 * 60 * 1000
        # Align ticks to wall-clock 5 minute marks (local time).
        offset_ms = (time.localtime(end_ms / 1000).tm_gmtoff or 0) * 1000
        first = ((start_ms + offset_ms) // step + 1) * step - offset_ms
        p.setFont(theme.mono_font(10))
        for t in range(first, end_ms, step):
            x = round(x_of(t)) + 0.5
            if x > right - 44:
                continue
            p.setPen(QPen(theme.color(theme.LINE, 150), 1))
            p.drawLine(QPointF(x, 0), QPointF(x, top))
            p.setPen(QColor(theme.DIM))
            p.drawText(QPointF(x + 4, top + 14), time.strftime("%H:%M", time.localtime(t / 1000)))
        p.setPen(QColor(theme.DIM))
        p.drawText(QRectF(left, top + 3, right - left, 14), Qt.AlignmentFlag.AlignRight, "now")
