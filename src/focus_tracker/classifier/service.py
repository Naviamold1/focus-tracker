"""Classifier: override > private > cache > (coalesced | Jev in background) with a local-tier fallback."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from focus_tracker.schema import Category, Classification, Clock, Surface

from .cache import DAY_MS, Entry, Store
from .jev import JevBackend, make_jev_backend
from .keys import bucket, display_label, embed_text, goal_hash, make_key, state_text
from .local import Embedder, LocalScorer, load_embedder
from .privacy import NeverSend

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[3]


@dataclass(slots=True)
class Settings:
    """`config["classifier"]` keys and their defaults."""

    g_hi: float = 0.21  # similarity to the goal at which a surface counts as productive
    margin: float = 0.12  # how much closer to a distraction anchor than to the goal means distraction
    min_confidence: float = (
        0.5  # Jev answers below this are discarded (local tier is used)
    )
    ttl_days: float = 7.0
    coalesce_cap: int = 5  # Jev requests per domain/app per trailing hour
    failure_cooldown_s: float = (
        60.0  # before retrying Jev for a key after total failure
    )
    low_confidence_cooldown_s: float = (
        300.0  # before re-asking Jev about a key it was unsure of
    )
    model_dir: str = str(REPO_ROOT / "models")
    jev_enabled: bool = True  # False forces local-only (no SDK import, no network)
    jev_model: str = "jev-latest"
    jev_timeout_s: float = 10.0
    workers: int = 2
    never_send: Mapping[str, list[str]] = field(default_factory=dict)

    @classmethod
    def from_config(cls, raw: Mapping[str, Any]) -> Settings:
        known = cls.__dataclass_fields__.keys()
        return cls(**{k: v for k, v in raw.items() if k in known})


class JevClassifier:
    """Implements `schema.Classifier` plus overrides and a cache inspector."""

    def __init__(
        self,
        data_dir: Path,
        settings: Settings,
        backend: JevBackend | None,
        embedder: Embedder | None,
        clock: Clock | None = None,
    ) -> None:
        self._s = settings
        self._backend = backend
        self._clock = clock or Clock()
        self._store = Store(data_dir / "classifications.json")
        self._never_send = NeverSend(settings.never_send)
        self._local = LocalScorer(embedder, settings.g_hi, settings.margin)
        self._pool = ThreadPoolExecutor(
            max_workers=settings.workers, thread_name_prefix="jev"
        )
        self._lock = threading.Lock()
        self._inflight: set[str] = set()
        self._retry_after: dict[
            str, int
        ] = {}  # key -> ms before which Jev is not re-asked
        self._unreachable = backend is None
        # Keys Jev answered during this run report source "jev"; entries loaded from disk report "cache".
        self._answered: set[str] = set()
        self.jev_last_ms: float | None = None  # last successful Jev round trip, for the UI

    @property
    def jev_online(self) -> bool:
        """True when a Jev backend exists and the last request (if any) did not fail."""
        return self._backend is not None and not self._unreachable

    # ------------------------------------------------------------- public API

    def classify(self, goal: str, surface: Surface) -> Classification:
        goal = goal.strip()
        key = make_key(goal, surface)
        now = self._clock.now_ms()
        if not goal:
            return Classification(
                key=key,
                category=None,
                source=None,
                confidence=None,
                pending=False,
                ts=now,
            )

        if (category := self._store.overrides.get(key)) is not None:
            return Classification(key, category, "override", 1.0, False, ts=now)
        if self._never_send.matches(surface):
            return Classification(key, "neutral", "private", 1.0, False, ts=now)

        ttl_ms = int(self._s.ttl_days * DAY_MS)
        if (entry := self._store.get(key, now, ttl_ms)) is not None:
            return Classification(
                key,
                entry["category"],
                "jev" if key in self._answered else "cache",
                entry["confidence"],
                False,
                ts=entry["ts"],
            )

        if self._backend is None:
            return self._local_answer(key, goal, surface, pending=False)

        name = bucket(surface)
        with self._lock:
            if key in self._inflight:
                return self._local_answer(key, goal, surface, pending=True)
            if now < self._retry_after.get(key, 0):
                return self._local_answer(key, goal, surface, pending=False)
            if self._store.request_count(name, now) >= self._s.coalesce_cap:
                return self._coalesced(key, goal, surface, name, ttl_ms)
            self._inflight.add(key)
            self._store.record_request(name, now)
        self._pool.submit(self._ask, key, goal, surface, name)
        return self._local_answer(key, goal, surface, pending=True)

    def on_goal_changed(self, goal: str) -> None:
        """New goal means new keys (goal hash is in the key); only transient cooldowns need resetting."""
        with self._lock:
            self._retry_after.clear()

    def set_override(self, key: str, category: Category) -> None:
        self._store.set_override(key, category)

    def clear_cache(self) -> None:
        self._store.clear()

    def cache_entries(self) -> list[dict[str, object]]:
        return self._store.snapshot()

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    # ------------------------------------------------------------- internals

    def _ask(self, key: str, goal: str, surface: Surface, name: str) -> None:
        """Worker thread: one Jev request, routed by confidence."""
        assert self._backend is not None
        now = self._clock.now_ms()
        try:
            started = time.perf_counter()
            result = self._backend.ask(state_text(goal, surface))
            self.jev_last_ms = (time.perf_counter() - started) * 1000
        except Exception as exc:  # noqa: BLE001 - any backend failure falls back to local
            # SDK already retried; log the type only (messages could echo request details).
            log.warning("Jev request failed (%s); using local tier", type(exc).__name__)
            self._unreachable = True
            self._finish(key, now + int(self._s.failure_cooldown_s * 1000))
            return
        self._unreachable = False
        if result.confidence >= self._s.min_confidence:
            self._store.put(
                key,
                Entry(
                    category=result.category,
                    source="jev",
                    confidence=result.confidence,
                    ts=self._clock.now_ms(),
                    bucket=name,
                    goal_hash=goal_hash(goal),
                    label=display_label(surface),
                ),
            )
            self._answered.add(key)
            self._finish(key, 0)
        else:
            self._finish(key, now + int(self._s.low_confidence_cooldown_s * 1000))

    def _finish(self, key: str, retry_after: int) -> None:
        with self._lock:
            self._inflight.discard(key)
            if retry_after:
                self._retry_after[key] = retry_after

    def _coalesced(
        self, key: str, goal: str, surface: Surface, name: str, ttl_ms: int
    ) -> Classification:
        """Cap hit: reuse the freshest answer for this domain/app (stale), else stay local."""
        now = self._clock.now_ms()
        entry = self._store.freshest(name, goal_hash(goal), now, ttl_ms)
        if entry is None:
            return self._local_answer(key, goal, surface, pending=False)
        return Classification(
            key,
            entry["category"],
            "cache",
            entry["confidence"],
            False,
            stale=True,
            ts=entry["ts"],
        )

    def _local_answer(
        self, key: str, goal: str, surface: Surface, *, pending: bool
    ) -> Classification:
        verdict = self._local.score(goal, embed_text(surface))
        return Classification(
            key,
            verdict.category,
            "local",
            verdict.confidence,
            pending,
            remote_unreachable=self._unreachable,
            ts=self._clock.now_ms(),
        )


def build_classifier(
    data_dir: Path,
    settings: dict[str, Any],
    *,
    backend: JevBackend | None = None,
    embedder: Embedder | None = None,
    clock: Clock | None = None,
) -> JevClassifier:
    """Public factory. `backend`/`embedder`/`clock` are injection points for tests and scripts;
    by default the real Jev client (if SDK + key exist) and the ONNX model from `model_dir` are used."""
    cfg = Settings.from_config(settings)
    if backend is None and cfg.jev_enabled:
        backend = make_jev_backend(cfg.jev_model, cfg.jev_timeout_s)
    if embedder is None:
        embedder = load_embedder(Path(cfg.model_dir))
    return JevClassifier(data_dir, cfg, backend, embedder, clock)
