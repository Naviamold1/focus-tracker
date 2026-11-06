"""M1.4 spike: classify 5 sample surfaces against Jev and print category, confidence, latency.

Needs `typesafe-sdk` installed and TYPESAFE_API_KEY set (environment or .env).
"""

from __future__ import annotations

import sys
import time

from dotenv import load_dotenv

from focus_tracker.classifier.jev import make_jev_backend
from focus_tracker.classifier.keys import state_text
from focus_tracker.schema import Surface

SAMPLES: list[tuple[str, Surface]] = [
    (
        "write the spec for the hackathon project",
        Surface("browser", "Google Chrome", "spec.md - Chrome", "github.com", "https://github.com/me/focus-tracker/blob/main/spec.md", "focus-tracker/spec.md at main"),
    ),
    (
        "study for the physics exam",
        Surface("browser", "Google Chrome", "Capacitor charging basics - Chrome", "youtube.com", "https://youtube.com/watch", "Capacitor charging basics explained"),
    ),
    (
        "study for the physics exam",
        Surface("browser", "Google Chrome", "funny cats 10 hours - Chrome", "youtube.com", "https://youtube.com/watch", "funny cats 10 hours"),
    ),
    (
        "editing the demo reel",
        Surface("app", "Discord", "#general | friends - Discord"),
    ),
    (
        "write the spec for the hackathon project",
        Surface("browser", "Google Chrome", "Omegle - Chrome", "omegle.com", "https://omegle.com/", "Omegle: Talk to strangers!"),
    ),
]  # fmt: skip


def main() -> int:
    load_dotenv()
    backend = make_jev_backend()
    if backend is None:
        print(
            "Jev unavailable: install typesafe-sdk and set TYPESAFE_API_KEY",
            file=sys.stderr,
        )
        return 1
    for goal, surface in SAMPLES:
        start = time.perf_counter()
        result = backend.ask(state_text(goal, surface))
        ms = (time.perf_counter() - start) * 1000
        print(
            f"{result.category:12} conf={result.confidence:.2f} {ms:6.0f} ms  [{goal}] {surface.label}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
