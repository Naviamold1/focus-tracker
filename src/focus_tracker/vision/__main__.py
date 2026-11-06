"""Manual webcam smoke test: `uv run python -m focus_tracker.vision [--calibrate] [seconds]`."""

import sys
import time

from focus_tracker.vision import VisionTracker


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    seconds = float(args[0]) if args else 15.0
    tracker = VisionTracker({"data_dir": "data"})
    tracker.start()
    if "--calibrate" in sys.argv:
        print("Calibrating: look at the screen for 3 s")
        tracker.calibrate()
    end = time.monotonic() + seconds
    try:
        while time.monotonic() < end:
            if (ev := tracker.latest()) is not None:
                print(f"present={ev.present} facing={ev.facing} yaw={ev.yaw:+6.1f} "
                      f"pitch={ev.pitch:+6.1f} fps={ev.fps:4.1f} degraded={ev.degraded}")
            time.sleep(0.5)
    finally:
        tracker.stop()
    print("baseline:", tracker.baseline())


if __name__ == "__main__":
    main()
