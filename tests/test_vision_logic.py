import cv2
import numpy as np
import pytest

from focus_tracker.vision.logic import (
    MODEL_POINTS,
    Baseline,
    BaselineCapture,
    FacingLimits,
    FlapGuard,
    PresenceFilter,
    box_from_landmarks,
    euler_from_rvec,
    is_facing,
    nose_in_box,
    project_axes,
    solve_head_pose,
)


def _rvec(yaw: float, pitch: float) -> np.ndarray:
    """Rotation vector for R = Ry(yaw) @ Rx(pitch), degrees."""
    y, p = np.radians(yaw), np.radians(pitch)
    ry = np.array([[np.cos(y), 0, np.sin(y)], [0, 1, 0], [-np.sin(y), 0, np.cos(y)]])
    rx = np.array([[1, 0, 0], [0, np.cos(p), -np.sin(p)], [0, np.sin(p), np.cos(p)]])
    return cv2.Rodrigues(ry @ rx)[0]


@pytest.mark.parametrize("yaw,pitch", [(0, 0), (25, 0), (-30, 10), (10, -15), (-15, 20)])
def test_euler_roundtrip(yaw: float, pitch: float) -> None:
    got = euler_from_rvec(_rvec(yaw, pitch))
    assert got == pytest.approx((yaw, pitch), abs=1e-6)


@pytest.mark.parametrize("yaw,pitch", [(0, 0), (20, 5), (-25, -10)])
def test_pnp_recovers_synthetic_pose(yaw: float, pitch: float) -> None:
    w, h = 640, 480
    camera = np.array([[w, 0, w / 2], [0, w, h / 2], [0, 0, 1]], dtype=np.float64)
    tvec = np.array([0.0, 0.0, 600.0])
    pts, _ = cv2.projectPoints(MODEL_POINTS, _rvec(yaw, pitch), tvec, camera, None)
    got = solve_head_pose(pts.reshape(-1, 2), w, h)
    assert got is not None
    assert got == pytest.approx((yaw, pitch), abs=1.5)


def test_facing_uses_baseline_relative_values() -> None:
    limits = FacingLimits()
    base = Baseline(yaw=10, pitch=-8)  # camera off-axis; user's screen gaze is (10, -8)
    assert is_facing(12 - base.yaw, -8 - base.pitch, limits)
    assert not is_facing(35 - base.yaw, -8 - base.pitch, limits)  # 25 deg off baseline
    assert not is_facing(10 - base.yaw, 15 - base.pitch, limits)  # pitch 23 above baseline
    assert is_facing(0, -15, limits)
    assert not is_facing(0, -15.1, limits)


def test_nose_in_box_fallback() -> None:
    eyes = ((0.4, 0.4), (0.6, 0.4))
    chin = (0.5, 0.7)
    assert nose_in_box((0.5, 0.52), *eyes, chin)
    assert not nose_in_box((0.58, 0.52), *eyes, chin)  # turned
    assert not nose_in_box((0.5, 0.7), *eyes, chin)  # nose at chin level


def test_presence_hysteresis() -> None:
    p = PresenceFilter()
    assert [p.update(True), p.update(True)] == [False, True]
    for _ in range(4):
        assert p.update(False)  # 4 misses: still present
    assert p.update(True)  # a hit resets the miss streak
    for _ in range(4):
        assert p.update(False)
    assert not p.update(False)  # 5th consecutive miss clears


def test_flap_guard_degrades_for_ten_minutes() -> None:
    g = FlapGuard()
    assert not g.update(False, 0.0)
    t = 0.0
    for i in range(6):  # 6 toggles in 12 s -> over the limit of 5
        t += 2
        degraded = g.update(i % 2 == 0, t)
    assert degraded
    assert g.update(True, t + 599)
    assert not g.update(True, t + 601)


def test_flap_guard_ignores_slow_toggles() -> None:
    g = FlapGuard()
    for i in range(20):
        assert not g.update(i % 2 == 0, i * 30.0)  # one toggle per 30 s


def test_baseline_capture_median_and_min_samples() -> None:
    c = BaselineCapture(started=0.0, duration_s=3.0)
    assert not c.done(2.9) and c.done(3.0)
    assert c.result() is None
    for yaw in (4, 5, 6, 5, 50):  # outlier rejected by median
        c.add(yaw, -3)
    assert c.result() == Baseline(5, -3)


def test_box_from_landmarks() -> None:
    pts = np.array([[10.0, 20.0], [50.0, 30.0], [30.0, 80.0]], dtype=np.float32)
    assert box_from_landmarks(pts) == (10.0, 20.0, 40.0, 60.0)


def test_project_axes_frontal_face() -> None:
    rvec, tvec = np.zeros(3), np.array([0.0, 0.0, 600.0])
    o, x, y, z = project_axes(rvec, tvec, 640, 480)
    assert o == pytest.approx((320.0, 240.0))
    assert x[0] > o[0] and x[1] == pytest.approx(o[1])  # x axis points image right
    assert y[1] > o[1] and y[0] == pytest.approx(o[0])  # y axis points image down
    assert z == pytest.approx(o)  # forward axis points at the camera, so it projects onto the nose
