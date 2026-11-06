"""Camera frame conversion and the MediaPipe FaceMesh index sets used by the overlay."""

from __future__ import annotations

import numpy as np
from PySide6.QtGui import QImage

# Ordered index loops/paths into the 478-point FaceMesh (standard MediaPipe topology).
FACE_OVAL = (
    10, 338, 297, 332, 284, 251, 389, 356, 454, 323, 361, 288, 397, 365, 379, 378, 400, 377, 152, 148,
    176, 149, 150, 136, 172, 58, 132, 93, 234, 127, 162, 21, 54, 103, 67, 109,
)  # fmt: skip
LEFT_EYE = (263, 249, 390, 373, 374, 380, 381, 382, 362, 398, 384, 385, 386, 387, 388, 466)
RIGHT_EYE = (33, 7, 163, 144, 145, 153, 154, 155, 133, 173, 157, 158, 159, 160, 161, 246)
LIPS_OUTER = (61, 146, 91, 181, 84, 17, 314, 405, 321, 375, 291, 409, 270, 269, 267, 0, 37, 39, 40, 185)
LIPS_INNER = (78, 95, 88, 178, 87, 14, 317, 402, 318, 324, 308, 415, 310, 311, 312, 13, 82, 81, 80, 191)
LEFT_BROW = (276, 283, 282, 295, 285)
RIGHT_BROW = (46, 53, 52, 65, 55)
LEFT_BROW_UPPER = (300, 293, 334, 296, 336)
RIGHT_BROW_UPPER = (70, 63, 105, 66, 107)

CLOSED_LOOPS = (FACE_OVAL, LEFT_EYE, RIGHT_EYE, LIPS_OUTER, LIPS_INNER)
OPEN_PATHS = (LEFT_BROW, RIGHT_BROW, LEFT_BROW_UPPER, RIGHT_BROW_UPPER)
NOSE_TIP = 1


def frame_to_qimage(rgb: np.ndarray) -> QImage:
    """HxWx3 uint8 RGB array to a QImage that owns its pixels."""
    data = np.ascontiguousarray(rgb, dtype=np.uint8)
    h, w, _ = data.shape
    return QImage(data.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()
