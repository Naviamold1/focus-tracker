"""Cache keys and the text derived from a Surface (keys, URL path, Jev state, embed text)."""

from __future__ import annotations

import hashlib
import re
from urllib.parse import urlsplit

from focus_tracker.schema import Surface

MAX_STATE_CHARS = 600
_NOTIF_PREFIX = re.compile(
    r"^\(\d+\)\s*"
)  # "(3) Inbox" -> "Inbox": notification counts churn titles


def short_hash(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()[:8]


def normalize_title(title: str | None) -> str:
    """Lowercase, collapse whitespace, drop leading notification counts."""
    return _NOTIF_PREFIX.sub("", " ".join((title or "").lower().split()))


def goal_hash(goal: str) -> str:
    return short_hash(goal.strip().lower())


def bucket(surface: Surface) -> str:
    """Coalescing/identity bucket: domain for browser surfaces, app name otherwise."""
    return (
        surface.domain if surface.kind == "browser" and surface.domain else surface.app
    ) or "unknown"


def surface_title(surface: Surface) -> str | None:
    return surface.page_title if surface.kind == "browser" else surface.window_title


def make_key(goal: str, surface: Surface) -> str:
    """`kind:bucket|title-hash|goal-hash`. Query/fragment never reach the key (domain and title only)."""
    title = short_hash(normalize_title(surface_title(surface)))
    return f"{surface.kind}:{bucket(surface)}|{title}|{goal_hash(goal)}"


def url_path(url: str | None, depth: int = 3) -> str:
    """host/path with query and fragment stripped, at most `depth` path segments."""
    if not url:
        return ""
    parts = urlsplit(url if "//" in url else f"//{url}")
    segments = [s for s in parts.path.split("/") if s][:depth]
    return "/".join([parts.netloc, *segments])


def _clip(text: str | None, n: int) -> str:
    return (text or "")[:n]


def state_text(goal: str, surface: Surface) -> str:
    """The only text sent to Jev (spec §5 fields), hard-capped at 600 chars."""
    lines = [
        f"Goal: {_clip(goal, 150)}",
        f"Surface kind: {surface.kind}",
        f"App: {_clip(surface.app, 60)}",
    ]
    if surface.kind == "browser":
        lines += [
            f"Domain: {_clip(surface.domain, 80)}",
            f"URL: {_clip(url_path(surface.url), 120)}",
            f"Page title: {_clip(surface.page_title, 120)}",
        ]
    lines.append(f"Window title: {_clip(surface.window_title, 120)}")
    return "\n".join(lines)[:MAX_STATE_CHARS]


def embed_text(surface: Surface) -> str:
    """Local-tier input: same fields Jev sees, minimal form."""
    head = (
        surface.domain if surface.kind == "browser" and surface.domain else surface.app
    )
    return f"{head or ''} {surface_title(surface) or ''}".strip()


def display_label(surface: Surface) -> str:
    """Label for the dashboard cache inspector: url-path (or app) plus title."""
    head = url_path(surface.url) or bucket(surface)
    return f"{head} | {_clip(surface_title(surface), 100)}".strip(" |")
