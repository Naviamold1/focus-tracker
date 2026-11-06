"""VisionTracker: capture + MediaPipe inference thread implementing schema.VisionLike.

Privacy: frames live only inside `_loop` and are dropped right after inference; nothing
is written to disk or sent anywhere (no imwrite, no sockets). Only VisionEvent numbers leave.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from focus_tracker.schema import FrameSnapshot, VisionEvent
from focus_tracker.vision.logic import (
    LANDMARK_IDS,
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
    solve_pnp,
)
from focus_tracker.vision.model import DEFAULT_MODEL_PATH, ensure_model

# Settings keys and defaults. The dict passed in is read live, so sliders can mutate it.
DEFAULTS: dict[str, Any] = {
    "camera_index": 0,
    "yaw_max": 20.0,
    "pitch_range": [-15.0, 20.0],  # [min, max] degrees relative to the baseline
    "fps": 8.0,  # inference rate, clamped to 5..10
    "data_dir": "data",  # baseline persisted to <data_dir>/vision_baseline.json
    "model_path": str(DEFAULT_MODEL_PATH),
}
FRAME_MAX_W = 640  # snapshot images are downscaled to this width
CALIBRATION_SECONDS = 3.0
CAMERA_RETRY_S = 10.0
MODEL_RETRY_S = 60.0
POSE_WARMUP_FRAMES = 3  # nose-in-box fallback until this many PnP samples are in
SMOOTHING = 0.5  # EMA weight of the newest pose sample

# Capture API per OS; CAP_ANY lets OpenCV choose elsewhere.
_CAMERA_BACKEND = {"win32": cv2.CAP_DSHOW, "linux": cv2.CAP_V4L2}.get(sys.platform, cv2.CAP_ANY)


def list_cameras() -> list[tuple[int, str]]:
    """(index, name) of capture devices, indices valid for this OS's capture backend.

    Uses cv2_enumerate_cameras (DirectShow on Windows, V4L2 on Linux). Probing does not open
    the devices, so it is safe while a camera is in use. Empty list on any failure."""
    try:
        from cv2_enumerate_cameras import enumerate_cameras

        return [(c.index, c.name) for c in enumerate_cameras(_CAMERA_BACKEND)]
    except Exception:  # noqa: BLE001  # enumeration is a convenience; never break the UI
        return []


class VisionTracker:
    def __init__(self, settings: dict[str, Any]) -> None:
        self._settings = settings
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._enabled = threading.Event()
        self._enabled.set()
        self._calibrate_requested = threading.Event()
        self._switch = threading.Event()  # set by set_camera(); ends the capture loop so _run reopens
        self._event: VisionEvent | None = None
        self._frame: FrameSnapshot | None = None  # replaced on each inference, never persisted
        self._frame_lock = threading.Lock()
        self._baseline = self._load_baseline()

    # ------------------------------------------------------------ public API

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="vision", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    def set_enabled(self, enabled: bool) -> None:
        """Disabling makes the thread release the camera (LED off) within one loop pass."""
        if enabled:
            self._enabled.set()
        else:
            self._enabled.clear()

    def calibrate(self) -> None:
        """Begin a 3 s baseline capture (look at the screen); needs the camera enabled."""
        self._calibrate_requested.set()

    def set_camera(self, index: int) -> None:
        """Switch to capture device `index` (from list_cameras()); takes effect within one loop pass."""
        self._settings["camera_index"] = int(index)
        self._switch.set()

    def baseline(self) -> Baseline:
        return self._baseline

    def latest(self) -> VisionEvent | None:
        return self._event

    def latest_frame(self) -> FrameSnapshot | None:
        """Last inferred frame with overlay data for the local window; None when the camera is off."""
        with self._frame_lock:
            return self._frame

    def _set_frame(self, frame: FrameSnapshot | None) -> None:
        with self._frame_lock:
            self._frame = frame

    # ------------------------------------------------------------ settings

    def _setting(self, key: str) -> Any:
        return self._settings.get(key, DEFAULTS[key])

    def _limits(self) -> FacingLimits:
        lo, hi = self._setting("pitch_range")
        return FacingLimits(float(self._setting("yaw_max")), float(lo), float(hi))

    def _baseline_file(self) -> Path:
        return Path(self._setting("data_dir")) / "vision_baseline.json"

    def _load_baseline(self) -> Baseline:
        try:
            data = json.loads(self._baseline_file().read_text())
            return Baseline(float(data["yaw"]), float(data["pitch"]))
        except (OSError, ValueError, KeyError, TypeError):
            return Baseline()

    def _save_baseline(self) -> None:
        try:
            path = self._baseline_file()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"yaw": self._baseline.yaw, "pitch": self._baseline.pitch}))
        except OSError:
            pass  # baseline stays in memory only

    # ------------------------------------------------------------ thread

    def _publish(self, *, present: bool = False, facing: bool | None = None, yaw: float = 0.0,
                 pitch: float = 0.0, fps: float = 0.0, degraded: bool = False) -> None:
        self._event = VisionEvent(present, facing, yaw, pitch, fps, degraded, int(time.time() * 1000))

    def _run(self) -> None:
        """Outer lifecycle: model, camera open/retry, enable/disable. Never raises."""
        landmarker: Any = None
        try:
            while not self._stop.is_set():
                if not self._enabled.is_set():
                    self._set_frame(None)
                    self._publish()  # camera off: not degraded, facing unknown
                    self._stop.wait(0.3)
                    continue
                if landmarker is None:
                    landmarker = self._make_landmarker()
                    if landmarker is None:
                        self._publish(degraded=True)
                        self._stop.wait(MODEL_RETRY_S)
                        continue
                self._switch.clear()
                cap = self._open_camera()
                if cap is None:
                    self._publish(degraded=True)
                    self._wait_retry(CAMERA_RETRY_S)
                    continue
                try:
                    self._loop(cap, landmarker)
                finally:
                    cap.release()  # LED off
                    self._set_frame(None)
        finally:
            if landmarker is not None:
                landmarker.close()

    def _wait_retry(self, seconds: float) -> None:
        """Sleep before retrying the camera, waking early on stop or a camera switch."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline and not (self._stop.is_set() or self._switch.is_set()):
            self._stop.wait(0.3)

    def _open_camera(self) -> cv2.VideoCapture | None:
        try:
            cap = cv2.VideoCapture(int(self._setting("camera_index")), _CAMERA_BACKEND)
        except cv2.error:
            return None
        if not cap.isOpened():
            cap.release()
            return None
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        return cap

    def _make_landmarker(self) -> Any:
        try:
            from mediapipe.tasks.python import BaseOptions, vision

            path = ensure_model(Path(self._setting("model_path")))
            if path is None:
                return None
            options = vision.FaceLandmarkerOptions(
                base_options=BaseOptions(model_asset_path=str(path)),
                running_mode=vision.RunningMode.VIDEO,
                num_faces=1,
                min_face_detection_confidence=0.5,
                min_face_presence_confidence=0.5,
                min_tracking_confidence=0.5,
            )
            return vision.FaceLandmarker.create_from_options(options)
        except Exception:  # noqa: BLE001  # broken model file or runtime error: degrade, never crash
            return None

    def _loop(self, cap: cv2.VideoCapture, landmarker: Any) -> None:
        """Inference loop. Returns when stopped, disabled, or the camera stops delivering."""
        import mediapipe as mp

        presence = PresenceFilter()
        guard = FlapGuard()
        capture: BaselineCapture | None = None
        yaw = pitch = 0.0  # smoothed raw pose
        pose_samples = 0
        fallback_facing = False
        next_infer = last_infer = fps = 0.0
        read_failures = 0

        while not self._stop.is_set() and self._enabled.is_set() and not self._switch.is_set():
            # grab() paces us at camera rate and is cheap; retrieve + inference only when due.
            if not cap.grab():
                read_failures += 1
                if read_failures > 30:
                    self._publish(degraded=True)
                    return
                time.sleep(0.03)
                continue
            read_failures = 0
            now = time.monotonic()
            if now < next_infer:
                continue
            ok, frame = cap.retrieve()
            if not ok:
                continue
            next_infer = now + 1.0 / min(10.0, max(5.0, float(self._setting("fps"))))
            if last_infer:
                inst = 1.0 / max(now - last_infer, 1e-3)
                fps = 0.7 * fps + 0.3 * inst if fps else inst
            last_infer = now

            h, w = frame.shape[:2]
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            del frame
            image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            result = landmarker.detect_for_video(image, int(now * 1000))
            del image
            solved = None
            all_px: np.ndarray | None = None

            hit = bool(result.face_landmarks)
            if hit:
                lms = result.face_landmarks[0]
                pts = np.array([[lms[i].x * w, lms[i].y * h] for i in LANDMARK_IDS])
                all_px = np.array([[p.x * w, p.y * h] for p in lms], dtype=np.float32)
                solved = solve_pnp(pts, w, h)
                pose = None if solved is None else euler_from_rvec(solved[0])
                if pose is None:
                    pose_samples = 0
                else:
                    if pose_samples == 0:
                        yaw, pitch = pose
                    else:
                        yaw = SMOOTHING * pose[0] + (1 - SMOOTHING) * yaw
                        pitch = SMOOTHING * pose[1] + (1 - SMOOTHING) * pitch
                    pose_samples += 1
                fallback_facing = nose_in_box(
                    (lms[1].x, lms[1].y), (lms[33].x, lms[33].y),
                    (lms[263].x, lms[263].y), (lms[152].x, lms[152].y),
                )
            present = presence.update(hit)
            if not present:
                pose_samples = 0
            degraded = guard.update(present, now)
            warm = pose_samples >= POSE_WARMUP_FRAMES

            if self._calibrate_requested.is_set() and capture is None:
                self._calibrate_requested.clear()
                capture = BaselineCapture(now, CALIBRATION_SECONDS)
            if capture is not None:
                if present and hit and warm:
                    capture.add(yaw, pitch)
                if capture.done(now):
                    new = capture.result()
                    if new is not None:
                        self._baseline = new
                        self._save_baseline()
                    capture = None

            base = self._baseline
            rel_yaw, rel_pitch = (yaw - base.yaw, pitch - base.pitch) if warm else (0.0, 0.0)
            if not present or degraded or capture is not None:
                facing = None
            elif warm:
                facing = is_facing(rel_yaw, rel_pitch, self._limits())
            else:
                facing = fallback_facing
            self._publish(present=present, facing=facing, yaw=rel_yaw, pitch=rel_pitch,
                          fps=fps, degraded=degraded)
            self._set_frame(self._snapshot(rgb, all_px, solved))

    @staticmethod
    def _snapshot(
        rgb: np.ndarray,
        landmarks_px: np.ndarray | None,
        solved: tuple[np.ndarray, np.ndarray] | None,
    ) -> FrameSnapshot:
        """Package the frame just inferred, downscaled to FRAME_MAX_W. Holds references only."""
        h, w = rgb.shape[:2]
        scale = min(1.0, FRAME_MAX_W / w)
        image = rgb
        if scale < 1.0:
            image = cv2.resize(rgb, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA)
        landmarks = None if landmarks_px is None else landmarks_px * np.float32(scale)
        box: tuple[float, float, float, float] | None = None
        nose: tuple[float, float] | None = None
        axes: tuple[tuple[float, float], tuple[float, float], tuple[float, float]] | None = None
        if landmarks is not None:
            box = box_from_landmarks(landmarks)
            nose = (float(landmarks[1, 0]), float(landmarks[1, 1]))
            if solved is not None:
                o, x, y, z = project_axes(solved[0], solved[1], w, h)
                # Anchor the axes on the detected nose tip rather than the model origin's projection.
                dx, dy = nose[0] - o[0] * scale, nose[1] - o[1] * scale
                axes = tuple((p[0] * scale + dx, p[1] * scale + dy) for p in (x, y, z))  # type: ignore[assignment]
        # MediaPipe's landmarker exposes no per-face score, so face_score stays None.
        return FrameSnapshot(image, landmarks, None, box, nose, axes, int(time.time() * 1000))
