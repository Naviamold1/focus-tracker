"""Fuse the foreground AppSignal with the latest BrowserEvent into a Surface."""

from __future__ import annotations

from dataclasses import dataclass

from focus_tracker.schema import AppSignal, BrowserEvent, Surface
from focus_tracker.watcher import BROWSER_PROCESSES, normalize_process

BROWSER_RECENCY_MS = 5_000
SWITCH_DWELL_MS = 2_000


def fuse(app: AppSignal | None, browser: BrowserEvent | None, now_ms: int) -> Surface | None:
    """Surface for this instant, or None before the watcher has produced anything.

    Browser kind needs a known browser in the foreground AND a browser event no
    older than 5 s (the extension heartbeat). Otherwise the browser is reported
    as a plain app surface (extension offline / incognito without access).
    """
    if app is None:
        return None
    is_browser = normalize_process(app.process) in BROWSER_PROCESSES
    if is_browser and browser is not None and 0 <= now_ms - browser.ts <= BROWSER_RECENCY_MS:
        return Surface(
            kind="browser",
            app=app.app,
            window_title=app.title,
            domain=browser.domain,
            url=browser.url,
            page_title=browser.page_title,
            incognito=browser.incognito,
        )
    return Surface(kind="app", app=app.app, window_title=app.title)


def _identity(s: Surface) -> tuple[object, ...]:
    """What counts as 'the same surface' for debouncing (title churn on apps is ignored)."""
    if s.kind == "browser":
        return (s.kind, s.app, s.domain, s.page_title)
    return (s.kind, s.app)


@dataclass(slots=True)
class SurfaceDebouncer:
    """Commits a new surface only after it has been seen continuously for 2 s.

    Kills Alt-Tab flicker. The first surface commits immediately.
    """

    dwell_ms: int = SWITCH_DWELL_MS
    committed: Surface | None = None
    _candidate: Surface | None = None
    _candidate_since: int = 0

    def update(self, observed: Surface | None, now_ms: int) -> Surface | None:
        """Feed the latest fused surface; returns the committed (debounced) one."""
        if observed is None:
            return self.committed
        if self.committed is None or _identity(observed) == _identity(self.committed):
            self.committed = observed  # same surface: refresh details such as titles
            self._candidate = None
            return self.committed
        if self._candidate is None or _identity(observed) != _identity(self._candidate):
            self._candidate, self._candidate_since = observed, now_ms
        else:
            self._candidate = observed
        if now_ms - self._candidate_since >= self.dwell_ms:
            self.committed, self._candidate = self._candidate, None
        return self.committed
