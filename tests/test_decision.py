"""State table, nudge ladder and caps (spec 4.4), driven on virtual time."""

from __future__ import annotations

import pytest
from conftest import CODE, YOUTUBE, Driver, classification, vision

from focus_tracker import demo
from focus_tracker.schema import Surface


def start_distracted(d: Driver, **changes: object) -> int:
    """Warm up on a productive surface, then switch to the distracting one. Returns start second."""
    d.advance(10)
    start = d.now // 1000
    d.advance(2, surface=YOUTUBE, cls=classification("distraction", key="yt"), **changes)
    return start


def test_productive_facing_is_focused_and_pi_rises(drive: Driver) -> None:
    ticks = drive.advance(300)
    assert ticks[-1][0].state == "focused"
    assert ticks[-1][0].pi > 50
    assert drive.nudges == []


def test_soft_nudge_at_dwell_then_toast_two_minutes_later(drive: Driver) -> None:
    start = start_distracted(drive)
    out = drive.advance(200)
    assert [lvl for _, lvl in drive.nudges] == ["soft", "escalated"]
    soft_t, esc_t = (t for t, _ in drive.nudges)
    assert 56 <= soft_t - start <= 62
    assert esc_t - soft_t == 120
    assert [e.toast for _, e in out if e.toast] and drive.nudges[0][0] < esc_t
    assert out[-1][0].state == "nudged"


@pytest.mark.parametrize(
    "cls",
    [
        classification("distraction", key="yt", confidence=0.7),
        classification("distraction", key="yt", confidence=0.95, source="local", remote_unreachable=True),
    ],
    ids=["mid-confidence", "local-tier"],
)
def test_dwell_doubles_for_mid_confidence_and_local(drive: Driver, cls: object) -> None:
    drive.advance(10)
    start = drive.now // 1000
    drive.advance(100, surface=YOUTUBE, cls=cls)
    assert drive.nudges == []
    drive.advance(30)
    assert 116 <= drive.nudges[0][0] - start <= 122


def test_stale_nudges_at_half_dwell_and_never_escalates(drive: Driver) -> None:
    start = start_distracted(drive, )
    drive.advance(0, cls=classification("distraction", key="yt", stale=True))
    drive.advance(400)
    assert drive.levels == ["soft"]
    assert drive.nudges[0][0] - start <= 34


def test_looking_away_is_quiet_thinking_and_resets_dwell(drive: Driver) -> None:
    start_distracted(drive)
    drive.advance(40)
    ticks = drive.advance(120, vis=vision(facing=False))
    assert {t.state for t, _ in ticks} == {"quiet_thinking"}
    drive.advance(50, vis=vision(facing=True))
    assert drive.nudges == []  # 40 s + 50 s must not add up across the away period


def test_switch_back_to_work_resets_dwell_without_nudging(drive: Driver) -> None:
    """Spec 10 #6: returning to productive work is never nudged."""
    start_distracted(drive)
    drive.advance(50)
    drive.advance(4, surface=CODE, cls=classification("productive"))
    drive.advance(50, surface=YOUTUBE, cls=classification("distraction", key="yt"))
    assert drive.nudges == []


def test_pending_classification_never_nudges_and_pi_holds(drive: Driver) -> None:
    drive.advance(10)
    pending = classification("distraction", key="yt", pending=True, source="local")
    ticks = drive.advance(300, surface=YOUTUBE, cls=pending)
    assert drive.nudges == []
    assert ticks[-1][0].classification_pending and ticks[-1][0].state == "focused"
    assert ticks[-1][0].pi == ticks[0][0].pi


def test_no_goal_is_observe_only(drive: Driver) -> None:
    ticks = drive.advance(300, goal="", surface=YOUTUBE, cls=None)
    assert drive.nudges == []
    last = ticks[-1][0]
    assert last.category == "unknown" and last.pi == 50
    assert last.surface_label == "youtube.com"


def test_per_surface_cooldown_blocks_renudge_for_five_minutes(drive: Driver) -> None:
    start = start_distracted(drive)
    drive.advance(60)  # first soft nudge fired
    drive.advance(4, surface=CODE, cls=classification("productive"))
    drive.advance(500, surface=YOUTUBE, cls=classification("distraction", key="yt"))
    times = [t for t, lvl in drive.nudges if lvl == "soft"]
    assert len(times) == 2
    assert times[1] - times[0] >= 300
    assert times[0] - start <= 62


def test_hour_cap_limits_nudges_across_surfaces(drive: Driver) -> None:
    drive.advance(10)
    for i in range(5):
        site = Surface(kind="app", app=f"Game{i}", window_title="x")
        drive.advance(70, surface=site, cls=classification("distraction", key=f"g{i}"))
        drive.advance(4, surface=CODE, cls=classification("productive"))
    assert drive.levels.count("soft") == 3


def test_snooze_suppresses_nudges_for_ten_minutes(drive: Driver) -> None:
    start_distracted(drive)
    drive.engine.snooze(drive.now)
    drive.advance(580)
    assert drive.nudges == []
    drive.advance(80)
    assert drive.levels == ["soft"]


def test_user_override_beats_classifier(drive: Driver) -> None:
    drive.engine.set_override("yt", "productive")
    start_distracted(drive)
    ticks = drive.advance(200)
    assert drive.nudges == []
    assert ticks[-1][0].category == "productive" and ticks[-1][0].category_source == "override"


def test_private_surface_is_neutral_and_not_scored(drive: Driver) -> None:
    private = classification("neutral", key="p", source="private", confidence=None)
    ticks = drive.advance(120, surface=YOUTUBE, cls=private)
    assert ticks[-1][0].pi == 50 and drive.nudges == []


def test_afk_releases_camera_and_restores_on_return(drive: Driver) -> None:
    drive.advance(10)
    away = drive.advance(4, idle=200.0, vis=vision(present=False, facing=None))
    assert away[0][0].state == "afk" and away[0][1].camera_release is True
    assert away[1][1].camera_release is None  # only the transition
    back = drive.advance(4, idle=0.0, vis=None)
    assert back[0][0].state == "focused" and back[0][1].camera_release is False


def test_long_idle_with_face_present_is_not_afk(drive: Driver) -> None:
    ticks = drive.advance(20, idle=400.0)
    assert ticks[-1][0].state == "focused"


def test_camera_off_uses_idle_only_and_still_nudges(drive: Driver) -> None:
    drive.advance(10)
    drive.advance(100, camera_on=False, vis=None, surface=YOUTUBE, cls=classification("distraction", key="yt"))
    assert drive.levels == ["soft"]
    afk = drive.advance(4, idle=200.0)
    assert afk[-1][0].state == "afk"


def test_demo_scenario_hits_every_state_through_the_real_engine() -> None:
    results = list(demo.replay())
    states = {t.state for t, _, _ in results}
    assert {"focused", "distracted", "nudged", "quiet_thinking", "afk"} <= states
    levels = [e.nudge_level for _, e, _ in results if e.nudge_level]
    assert levels == ["soft", "escalated", "soft"]
    # no nudge while the user is back on productive work (#6)
    for tick, effects, _ in results:
        if tick.category == "productive":
            assert effects.nudge_level is None
