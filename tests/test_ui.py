"""Qt window pieces that carry logic: trace history, run merging, frame conversion, window wiring."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from focus_tracker.ui.fake import FakeEngine, make_frame, make_state
from focus_tracker.ui.frames import frame_to_qimage
from focus_tracker.ui.traces import WINDOW_MS, Sample, TraceHistory, runs
from focus_tracker.ui.window import MainWindow


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def sample(ts: int, app: str = "VS Code") -> Sample:
    return Sample(
        ts=ts, app=app, cam_on=True, present=True, yaw=0.0, yaw_limit=20.0,
        category="productive", source="jev", confidence=0.9, state="focused",
    )  # fmt: skip


def test_history_trims_to_25_minutes() -> None:
    h = TraceHistory()
    for i in range(0, 40 * 60, 2):  # 40 minutes of 2 s ticks
        h.add(sample(i * 1000))
    first, last = h.samples[0].ts, h.samples[-1].ts
    assert last - first <= WINDOW_MS
    assert last - first > WINDOW_MS - 2000


def test_history_ignores_duplicate_timestamps() -> None:
    h = TraceHistory()
    h.add(sample(1000))
    h.add(sample(1000))
    assert len(h) == 1


def test_runs_merge_contiguous_and_split_on_gap() -> None:
    same = [sample(0), sample(2000), sample(4000)]
    assert [(a, b) for a, b, _ in runs(same, lambda s: s.app)] == [(0, 6000)]
    gap = [sample(0), sample(2000), sample(60_000)]
    assert len(runs(gap, lambda s: s.app)) == 2  # a long hole is not bridged


def test_frame_to_qimage_keeps_rgb(qapp: QApplication) -> None:
    arr = np.zeros((4, 6, 3), np.uint8)
    arr[1, 2] = (10, 200, 30)
    img = frame_to_qimage(arr)
    assert (img.width(), img.height()) == (6, 4)
    assert img.format() == QImage.Format.Format_RGB888
    px = img.pixelColor(2, 1)
    assert (px.red(), px.green(), px.blue()) == (10, 200, 30)


def test_fake_frame_matches_contract() -> None:
    snap = make_frame(1)
    assert snap.landmarks is not None and snap.landmarks.shape == (478, 2)
    assert snap.image.shape[2] == 3 and snap.image.dtype == np.uint8


def test_window_sends_commands_and_banner(qapp: QApplication) -> None:
    engine = FakeEngine()
    w = MainWindow(engine)
    w.apply(make_state(0, ts_ms=1_000_000))
    assert w._banner.isHidden()
    nudge_state = next(make_state(i, ts_ms=2_000_000) for i in range(2000) if make_state(i, ts_ms=0).extra["nudge_text"])
    w.apply(nudge_state)
    assert not w._banner.isHidden()
    w.goal.setText("Edit video")
    w._submit_goal()
    w.btn_snooze.click()
    assert engine.commands == [{"cmd": "set_goal", "goal": "Edit video"}, {"cmd": "snooze"}]
