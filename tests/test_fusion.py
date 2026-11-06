from focus_tracker.fusion import SurfaceDebouncer, fuse
from focus_tracker.schema import AppSignal, BrowserEvent, Surface


def app(process: str = "chrome.exe", label: str = "Google Chrome") -> AppSignal:
    return AppSignal(app=label, process=process, title="t", input_idle_sec=0, ts=0)


def page(ts: int, domain: str | None = "youtube.com", incognito: bool = False) -> BrowserEvent:
    return BrowserEvent(
        url="youtube.com/watch", domain=domain, page_title="cats", audible=False, incognito=incognito, ts=ts
    )


def test_browser_surface_when_browser_foreground_and_event_fresh() -> None:
    s = fuse(app(), page(ts=10_000), now_ms=14_000)
    assert s is not None and s.kind == "browser" and s.label == "youtube.com" and s.page_title == "cats"


def test_stale_browser_event_falls_back_to_app() -> None:
    s = fuse(app(), page(ts=0), now_ms=5_001)
    assert s is not None and s.kind == "app" and s.label == "Google Chrome"


def test_browser_event_ignored_when_other_app_foreground() -> None:
    s = fuse(app("code.exe", "Visual Studio Code"), page(ts=1_000), now_ms=1_500)
    assert s is not None and s.kind == "app" and s.domain is None


def test_linux_browser_process_names_count_as_browsers() -> None:
    s = fuse(app("google-chrome", "Google Chrome"), page(ts=10_000), now_ms=11_000)
    assert s is not None and s.kind == "browser"


def test_incognito_flag_carried_and_no_app_gives_none() -> None:
    s = fuse(app(), page(0, incognito=True), 0)
    assert s is not None and s.incognito
    assert fuse(None, page(0), 0) is None


def test_debouncer_ignores_short_flicker_and_commits_after_two_seconds() -> None:
    code = Surface(kind="app", app="Code", window_title="a")
    chat = Surface(kind="app", app="Discord", window_title="b")
    d = SurfaceDebouncer()
    assert d.update(code, 0) == code  # first surface commits immediately
    assert d.update(chat, 1_000) == code  # Alt-Tab flicker
    assert d.update(code, 1_500) == code  # back before 2 s: candidate dropped
    assert d.update(chat, 2_000) == code
    assert d.update(chat, 3_900) == code
    assert d.update(chat, 4_000) == chat


def test_title_churn_on_same_app_does_not_switch() -> None:
    d = SurfaceDebouncer()
    d.update(Surface(kind="app", app="Code", window_title="a.py"), 0)
    renamed = Surface(kind="app", app="Code", window_title="a.py *")
    assert d.update(renamed, 100) == renamed  # refreshed in place, still the same surface
