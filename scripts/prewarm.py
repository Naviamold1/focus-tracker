"""Cache pre-warm: run a demo surface list through the classifier so answers are cached before a demo.

Default list is tests/fixtures/surfaces.json (goal + surface pairs); pass --surfaces for your own.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from dotenv import load_dotenv

from focus_tracker.classifier import build_classifier
from focus_tracker.config import data_dir
from focus_tracker.schema import Surface

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=data_dir())  # same dir the engine reads
    parser.add_argument(
        "--surfaces", type=Path, default=ROOT / "tests" / "fixtures" / "surfaces.json"
    )
    args = parser.parse_args()

    clf = build_classifier(args.data_dir, {})
    rows = json.loads(args.surfaces.read_text(encoding="utf-8"))
    pending = [(r["goal"], Surface(**r["surface"])) for r in rows]
    deadline = time.monotonic() + 120
    while pending and time.monotonic() < deadline:
        # Re-call until each answer lands (classify is non-blocking); coalescing may cap a domain.
        pending = [(g, s) for g, s in pending if clf.classify(g, s).pending]
        time.sleep(0.3)
    clf.close()
    for row in clf.cache_entries():
        print(
            f"{row['category']:12} {row['source']:8} conf={row['confidence']:.2f} {row['label']}"
        )
    print(
        f"{len(clf.cache_entries())} cached in {args.data_dir / 'classifications.json'}"
    )
    if pending:
        print(f"{len(pending)} surfaces still pending (Jev unreachable or slow)")


if __name__ == "__main__":
    main()
