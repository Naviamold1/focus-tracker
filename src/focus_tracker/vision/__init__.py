"""Vision component: webcam presence + head-pose facing. Public API is VisionTracker."""

from focus_tracker.vision.tracker import DEFAULTS, VisionTracker, list_cameras

__all__ = ["DEFAULTS", "VisionTracker", "list_cameras"]
