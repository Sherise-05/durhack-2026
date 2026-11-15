from datetime import timedelta

import pytest
from factories import at, event, short_last_night, user

from app.schemas import AgeBand, EventCategory, Tiredness
from app.services.nap_recc import best_nap_slot, fuse_tiredness, recommend_nap


def tired_user(**kw):
    return user(short_last_night(), **kw)


def test_power_nap_after_a_short_night():
    r = recommend_nap(tired_user(), at(0, "13:30"), Tiredness.TIRED)
    assert r.should_nap and r.kind == "power" and r.sleep_min in (10, 20)
    assert r.alarm - r.lie_down <= timedelta(minutes=50)  # time to fall asleep + the nap


@pytest.mark.parametrize("hour", ["11:00", "12:30", "13:00", "14:00", "15:30", "17:00"])
@pytest.mark.parametrize("tiredness", [None, *Tiredness])
def test_never_plans_30_to_60_minute_naps(hour, tiredness):
    r = recommend_nap(tired_user(), at(0, hour), tiredness)
    assert r.sleep_min in (0, 10, 20, 90)


def test_90_minutes_only_when_very_tired_in_early_afternoon():
    assert recommend_nap(tired_user(), at(0, "13:00"), Tiredness.VERY_TIRED).sleep_min == 90
    assert recommend_nap(tired_user(), at(0, "13:00"), Tiredness.TIRED).sleep_min != 90
    assert recommend_nap(tired_user(), at(0, "16:00"), Tiredness.VERY_TIRED).sleep_min != 90


def test_every_nap_shows_its_cost_to_tonight():
    power = recommend_nap(tired_user(), at(0, "13:00"), Tiredness.TIRED)
    full = recommend_nap(tired_user(), at(0, "13:00"), Tiredness.VERY_TIRED)
    assert any(r.startswith("Cost to tonight") for r in power.reasons)
    assert 0 < power.tonight_cost_pct < full.tonight_cost_pct


def test_naps_after_3pm_get_a_warning():
    r = recommend_nap(tired_user(), at(0, "16:00"), Tiredness.TIRED)
    assert any("3 p.m." in w for w in r.warnings)


def test_alarm_leaves_20_minutes_before_an_exam():
    exam = event(0, "15:00", "17:00", EventCategory.EXAM, title="Stats exam", travel_min=10)
    r = recommend_nap(tired_user(events=[exam]), at(0, "14:00"), Tiredness.TIRED)
    assert r.alarm is not None and r.alarm <= exam.start - timedelta(minutes=30)  # 10 travel + 20 grogginess
    assert any("Give yourself 20 minutes" in w for w in r.warnings)


def test_no_nap_when_the_next_event_is_too_close():
    lecture = event(0, "14:20", "15:00", EventCategory.LECTURE)
    r = recommend_nap(tired_user(events=[lecture]), at(0, "14:00"), Tiredness.TIRED)
    assert not r.should_nap and r.kind == "none" and r.alarm is None


def test_no_nap_during_an_event():
    lab = event(0, "13:00", "15:00", EventCategory.STUDY, title="Lab")
    assert recommend_nap(tired_user(events=[lab]), at(0, "14:00"), Tiredness.TIRED).kind == "none"


def test_no_nap_close_to_bedtime():
    r = recommend_nap(tired_user(), at(0, "21:45"), Tiredness.VERY_TIRED)
    assert r.kind == "none" and "bed" in r.headline


def test_over_65s_get_no_late_afternoon_naps():
    older = tired_user(age_band=AgeBand.OLDER)
    assert recommend_nap(older, at(0, "16:00"), Tiredness.VERY_TIRED).kind == "none"
    assert recommend_nap(older, at(0, "13:30"), Tiredness.TIRED).kind != "none"


def test_feeling_fine_cannot_cancel_sleep_debt():
    assert fuse_tiredness(0.6, Tiredness.FINE) == 0.6
    assert fuse_tiredness(0.4, Tiredness.VERY_TIRED) > 0.5
    assert fuse_tiredness(0.4, None) == 0.4


def test_with_nap_curve_beats_without_an_hour_after_waking():
    r = recommend_nap(tired_user(), at(0, "13:30"), Tiredness.TIRED)
    t = r.alarm + timedelta(minutes=60)
    with_nap = next(p.alertness for p in r.with_nap if p.at >= t)
    without = next(p.alertness for p in r.without_nap if p.at >= t)
    assert with_nap > without


def test_best_slot_is_in_the_early_afternoon():
    slot = best_nap_slot(tired_user(), at(0, "09:00"))
    assert slot is not None and slot.should_nap
    assert at(0, "12:00") <= slot.lie_down <= at(0, "15:00")
    assert slot.headline.startswith("Best nap today")
