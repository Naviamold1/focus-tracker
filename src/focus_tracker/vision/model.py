"""Fetches the MediaPipe face_landmarker.task model on first use (model only, never images)."""

from __future__ import annotations

import urllib.request
from pathlib import Path

MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/latest/"
    "face_landmarker.task"
)
# <repo>/models, gitignored.
DEFAULT_MODEL_PATH = Path(__file__).resolve().parents[3] / "models" / "face_landmarker.task"


def ensure_model(path: Path = DEFAULT_MODEL_PATH) -> Path | None:
    """Return the model path, downloading it if absent. None on any failure (caller degrades)."""
    if path.exists():
        return path
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        with urllib.request.urlopen(MODEL_URL, timeout=30) as resp, tmp.open("wb") as out:
            while chunk := resp.read(1 << 16):
                out.write(chunk)
        tmp.replace(path)
        return path
    except OSError:
        return None
