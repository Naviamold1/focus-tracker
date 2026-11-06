"""Never-send filter (spec §4.2.6): private surfaces are never sent to Jev nor embedded."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from focus_tracker.schema import Surface

DEFAULT_APPS = (
    "1password",
    "bitwarden",
    "keepass",
    "keepassxc",
    "lastpass",
    "dashlane",
    "nordpass",
    "proton pass",
)
DEFAULT_DOMAINS = (
    "1password.com",
    "bitwarden.com",
    "lastpass.com",
    "dashlane.com",
    "nordpass.com",
    "paypal.com",
    "chase.com",
    "bankofamerica.com",
    "wellsfargo.com",
    "citi.com",
    "capitalone.com",
    "americanexpress.com",
    "schwab.com",
    "fidelity.com",
    "revolut.com",
    "wise.com",
    "monzo.com",
    "coinbase.com",
)
# Covers browsers whose extension cannot see incognito tabs (only the window title is visible).
_PRIVATE_TITLE_MARKERS = ("incognito", "inprivate", "private browsing")


class NeverSend:
    """Built-in list merged with user patterns (`{"apps": [...], "domains": [...]}`).

    The user mapping is held by reference and read on every match, so the dashboard's
    "exclude this surface" button (which appends to the config lists) applies immediately."""

    def __init__(self, user: Mapping[str, Iterable[str]] | None = None) -> None:
        self._user = user if user is not None else {}

    def matches(self, surface: Surface) -> bool:
        if surface.incognito:
            return True
        title = (surface.window_title or "").lower()
        if any(m in title for m in _PRIVATE_TITLE_MARKERS):
            return True
        app = (surface.app or "").lower()
        apps = (*DEFAULT_APPS, *self._user.get("apps", ()))
        if app and any(a.lower() in app for a in apps):
            return True
        domain = (surface.domain or "").lower()
        domains = (*DEFAULT_DOMAINS, *self._user.get("domains", ()))
        return bool(domain) and any(domain == d.lower() or domain.endswith(f".{d.lower()}") for d in domains)
