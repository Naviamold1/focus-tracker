"""Pure vision logic: no camera, no threads, no MediaPipe. Everything here is unit-tested."""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from statistics import median

import cv2
import numpy as np

# 6-point classic face model in OpenCV camera axes (x right, y down, z away from camera),
# millimetres, nose tip at the origin. Identity rotation = face looking straight at the camera.
MODEL_POINTS = np.array(
    [
        (0.0, 0.0, 0.0),  # nose tip
        (0.0, 330.0, 65.0),  # chin
        (-225.0, -170.0, 135.0),  # eye outer corner, image left
        (225.0, -170.0, 135.0),  # eye outer corner, image right
        (-150.0, 150.0, 125.0),  # mouth corner, image left
        (150.0, 150.0, 125.0),  # mouth corner, image right
    ],
    dtype=np.float64,
)
# MediaPipe face mesh indices matching MODEL_POINTS order.
LANDMARK_IDS = (1, 152, 33, 263, 61, 291)
AXIS_LENGTH_MM = 150.0  # head-axis overlay length, about half a face width in model units


@dataclass(frozen=True, slots=True)
class Baseline:
    """Yaw/pitch (degrees) measured while the user looks at the screen."""

    yaw: float = 0.0
    pitch: float = 0.0


@dataclass(frozen=True, slots=True)
class FacingLimits:
    """Facing box, applied as offsets from the calibration baseline."""

    yaw_max: float = 20.0
    pitch_min: float = -15.0
    pitch_max: float = 20.0


def euler_from_rvec(rvec: np.ndarray) -> tuple[float, float]:
    """(yaw, pitch) in degrees from a solvePnP rotation vector, roll-invariant.

    Yaw is positive when the face turns toward image right, pitch positive when the
    head tilts up. Assumes R = Rz(roll) @ Ry(yaw) @ Rx(pitch).
    """
    rot, _ = cv2.Rodrigues(np.asarray(rvec, dtype=np.float64))
    yaw = math.atan2(-rot[2, 0], math.hypot(rot[2, 1], rot[2, 2]))
    pitch = math.atan2(rot[2, 1], rot[2, 2])
    return math.degrees(yaw), math.degrees(pitch)


def solve_pnp(points_px: np.ndarray, frame_w: int, frame_h: int) -> tuple[np.ndarray, np.ndarray] | None:
    """(rvec, tvec) from 6 image points ordered as LANDMARK_IDS, or None if PnP fails."""
    ok, rvec, tvec = cv2.solvePnP(
        MODEL_POINTS, points_px.astype(np.float64), _camera_matrix(frame_w, frame_h), None,
        flags=cv2.SOLVEPNP_ITERATIVE,
    )
    return (rvec, tvec) if ok else None


def _camera_matrix(frame_w: int, frame_h: int) -> np.ndarray:
    focal = float(frame_w)
    return np.array([[focal, 0, frame_w / 2], [0, focal, frame_h / 2], [0, 0, 1]], dtype=np.float64)


def solve_head_pose(points_px: np.ndarray, frame_w: int, frame_h: int) -> tuple[float, float] | None:
    """(yaw, pitch) degrees from 6 image points ordered as LANDMARK_IDS, or None if PnP fails."""
    solved = solve_pnp(points_px, frame_w, frame_h)
    return None if solved is None else euler_from_rvec(solved[0])


def project_axes(
    rvec: np.ndarray, tvec: np.ndarray, frame_w: int, frame_h: int, length: float = AXIS_LENGTH_MM
) -> tuple[tuple[float, float], tuple[float, float], tuple[float, float], tuple[float, float]]:
    """Image points (origin, x, y, forward) of the head axes drawn from the nose tip.

    Forward is -z in model space (out of the face toward the camera). Coordinates are in
    the pixel space of the frame the pose was solved on.
    """
    pts3d = np.array([(0, 0, 0), (length, 0, 0), (0, length, 0), (0, 0, -length)], dtype=np.float64)
    proj, _ = cv2.projectPoints(pts3d, rvec, tvec, _camera_matrix(frame_w, frame_h), None)
    o, x, y, z = ((float(p[0]), float(p[1])) for p in proj.reshape(4, 2))
    return o, x, y, z


def box_from_landmarks(landmarks: np.ndarray) -> tuple[float, float, float, float]:
    """(x, y, w, h) bounding box of an (N, 2) landmark array."""
    lo = landmarks.min(axis=0)
    hi = landmarks.max(axis=0)
    return float(lo[0]), float(lo[1]), float(hi[0] - lo[0]), float(hi[1] - lo[1])


def is_facing(yaw: float, pitch: float, limits: FacingLimits) -> bool:
    """yaw/pitch are already relative to the baseline."""
    return abs(yaw) < limits.yaw_max and limits.pitch_min <= pitch <= limits.pitch_max


def nose_in_box(nose: tuple[float, float], eye_l: tuple[float, float], eye_r: tuple[float, float],
                chin: tuple[float, float]) -> bool:
    """Cheap facing fallback while PnP warms up: is the nose near the face's center?

    Inputs are normalized image coords. Nose must sit horizontally between the eye
    corners' midpoint (+-15% of eye span) and vertically in the middle band eyes->chin.
    """
    span = abs(eye_r[0] - eye_l[0])
    if span < 1e-6:
        return False
    nx = (nose[0] - (eye_l[0] + eye_r[0]) / 2) / span
    eye_y = (eye_l[1] + eye_r[1]) / 2
    height = chin[1] - eye_y
    if height < 1e-6:
        return False
    ny = (nose[1] - eye_y) / height
    return abs(nx) < 0.15 and 0.3 <= ny <= 0.65


class PresenceFilter:
    """Hysteresis: confirm after `confirm` consecutive hits, clear after `clear` consecutive misses."""

    def __init__(self, confirm: int = 2, clear: int = 5) -> None:
        self._confirm, self._clear = confirm, clear
        self._hits = self._misses = 0
        self.present = False

    def update(self, hit: bool) -> bool:
        if hit:
            self._hits, self._misses = self._hits + 1, 0
            if not self.present and self._hits >= self._confirm:
                self.present = True
        else:
            self._misses, self._hits = self._misses + 1, 0
            if self.present and self._misses >= self._clear:
                self.present = False
        return self.present


class FlapGuard:
    """More than `max_toggles` presence toggles within `window_s` -> degraded for `cooldown_s`."""

    def __init__(self, max_toggles: int = 5, window_s: float = 60.0, cooldown_s: float = 600.0) -> None:
        self._max, self._window, self._cooldown = max_toggles, window_s, cooldown_s
        self._toggles: deque[float] = deque()
        self._last: bool | None = None
        self._until = 0.0

    def update(self, present: bool, now: float) -> bool:
        """Feed the confirmed presence each frame; returns True while degraded."""
        if self._last is not None and present != self._last:
            self._toggles.append(now)
        self._last = present
        while self._toggles and now - self._toggles[0] > self._window:
            self._toggles.popleft()
        if len(self._toggles) > self._max:
            self._until = now + self._cooldown
            self._toggles.clear()
        return now < self._until


class BaselineCapture:
    """Collects raw yaw/pitch samples for `duration_s`, then yields their median."""

    MIN_SAMPLES = 5

    def __init__(self, started: float, duration_s: float = 3.0) -> None:
        self._end = started + duration_s
        self._yaw: list[float] = []
        self._pitch: list[float] = []

    def add(self, yaw: float, pitch: float) -> None:
        self._yaw.append(yaw)
        self._pitch.append(pitch)

    def done(self, now: float) -> bool:
        return now >= self._end

    def result(self) -> Baseline | None:
        """None when too few samples were seen (user not in frame); keep the old baseline."""
        if len(self._yaw) < self.MIN_SAMPLES:
            return None
        return Baseline(median(self._yaw), median(self._pitch))
