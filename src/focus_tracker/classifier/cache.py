"""Persistent classification cache, user overrides and the per-domain request log (classifications.json)."""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import TypedDict

from focus_tracker.schema import Category

log = logging.getLogger(__name__)

HOUR_MS = 3_600_000
DAY_MS = 24 * HOUR_MS


class Entry(TypedDict):
    category: Category
    source: str  # "jev" for cached remote answers
    confidence: float
    ts: int
    bucket: str  # domain or app, for coalescing
    goal_hash: str
    label: str  # url-path / title shown in the cache inspector


class Store:
    """All state behind one lock; every mutation is written through atomically."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.RLock()
        self.entries: dict[str, Entry] = {}
        self.overrides: dict[str, Category] = {}
        self.requests: dict[str, list[int]] = {}  # bucket -> request timestamps
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            self.entries = raw.get("entries", {})
            self.overrides = raw.get("overrides", {})
            self.requests = raw.get("requests", {})
        except FileNotFoundError:
            pass
        except (OSError, ValueError, AttributeError):
            log.warning("could not read %s; starting with an empty cache", self._path)

    def _save(self) -> None:
        data = {
            "entries": self.entries,
            "overrides": self.overrides,
            "requests": self.requests,
        }
        tmp = self._path.with_suffix(".tmp")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(data), encoding="utf-8")
            os.replace(tmp, self._path)
        except OSError:
            log.warning("could not write %s", self._path)

    def get(self, key: str, now: int, ttl_ms: int) -> Entry | None:
        with self._lock:
            entry = self.entries.get(key)
            return entry if entry and now - entry["ts"] <= ttl_ms else None

    def put(self, key: str, entry: Entry) -> None:
        with self._lock:
            self.entries[key] = entry
            self._save()

    def freshest(
        self, bucket: str, goal_hash: str, now: int, ttl_ms: int
    ) -> Entry | None:
        """Newest live entry of this bucket for the current goal (coalescing fallback)."""
        with self._lock:
            live = [
                e
                for e in self.entries.values()
                if e["bucket"] == bucket
                and e["goal_hash"] == goal_hash
                and now - e["ts"] <= ttl_ms
            ]
            return max(live, key=lambda e: e["ts"], default=None)

    def request_count(self, bucket: str, now: int) -> int:
        with self._lock:
            return sum(1 for t in self.requests.get(bucket, ()) if now - t < HOUR_MS)

    def record_request(self, bucket: str, now: int) -> None:
        with self._lock:
            recent = [t for t in self.requests.get(bucket, ()) if now - t < HOUR_MS]
            self.requests[bucket] = [*recent, now]
            self._save()

    def set_override(self, key: str, category: Category) -> None:
        with self._lock:
            self.overrides[key] = category
            self._save()

    def clear(self) -> None:
        """Drop cached answers and request log; user overrides survive."""
        with self._lock:
            self.entries.clear()
            self.requests.clear()
            self._save()

    def snapshot(self) -> list[dict[str, object]]:
        """Rows for the dashboard cache inspector, newest first. Overrides win over cached rows."""
        with self._lock:
            rows: dict[str, dict[str, object]] = {
                key: {
                    "key": key,
                    "category": e["category"],
                    "source": e["source"],
                    "confidence": e["confidence"],
                    "ts": e["ts"],
                    "label": e["label"],
                }
                for key, e in self.entries.items()
            }
            for key, category in self.overrides.items():
                base = rows.get(key, {"ts": 0, "label": ""})
                rows[key] = {
                    **base,
                    "key": key,
                    "category": category,
                    "source": "override",
                    "confidence": 1.0,
                }
            return sorted(rows.values(), key=lambda r: r["ts"], reverse=True)  # type: ignore[arg-type,return-value]
