"""Planner acceptance cases from the independent QA review."""
from datetime import timedelta

import pytest
from factories import at, event, nap, rested_nights, user

from app.schemas import AgeBand, CalendarEvent, EventCategory, Mode
from app.services import planner, sleep_model as sm


def test_impossible_night_shift_is_explicit_and_chart_still_renders():
    shift = CalendarEvent(title="Night shift", start=at(0, "23:00"), end=at(1, "09:00"))
    data = user(events=[shift])
    now = at(0, "20:00")
    plan = planner.recommend_bedtime(data, now)
    assert not plan.feasible
    assert plan.sleep_h == 0
    assert "No sleep window" in plan.headline
    assert any("No sleep window" in warning for warning in plan.warnings)
    curve = planner.day_curve(data, now)
    assert curve and all(point.alertness is not None for point in curve)


def test_already_passed_early_flight_deadline_is_explained_without_chart_crash():
    flight = event(1, "03:00", "04:00", EventCategory.TRAVEL, title="Flight")
    data = user(events=[flight])
    now = at(1, "03:00")
    plan = planner.recommend_bedtime(data, now)
    assert not plan.feasible
    assert plan.alarm < now
    assert any("already passed" in warning for warning in plan.warnings)
    assert planner.day_curve(data, now)


def test_feasible_plan_keeps_existing_headline_and_alarm_contract():
    plan = planner.recommend_bedtime(user(), at(0, "20:00"))
    assert plan.feasible and plan.bedtime < plan.alarm
    assert plan.headline.startswith("Lights out")


def test_day_chart_keeps_morning_after_a_nap_without_changing_inertia_wake():
    data = user(rested_nights() + [nap(0, "14:00", 20)])
    now = at(0, "16:00")
    model = sm.PersonModel(data, now)
    assert model.state.woke_at == at(0, "14:20")
    assert planner.day_curve(data, now, model)[0].at == at(0, "07:30")


@pytest.mark.parametrize("start,end", [("09:02", "09:12"), ("09:02", "09:03")])
def test_short_exam_receives_forecast_independently_of_display_grid(start, end):
    oral = event(1, start, end, EventCategory.EXAM, title="Oral exam")
    result = planner.what_if_bedtime(
        user(events=[oral]), at(0, "20:00"), at(0, "22:30"), at(1, "07:30"),
    )
    assert [forecast.title for forecast in result.events] == ["Oral exam"]
    assert 0 <= result.events[0].alertness <= 100


def test_work_underway_on_waking_has_conflict_warning_and_remaining_forecast():
    work = event(1, "07:00", "09:00", EventCategory.WORK, title="Work")
    result = planner.what_if_bedtime(
        user(events=[work]), at(0, "20:00"), at(0, "22:30"), at(1, "07:30"),
    )
    assert any("overlaps Work" in warning for warning in result.warnings)
    assert [forecast.title for forecast in result.events] == ["Work"]


def test_event_slept_through_is_explained_instead_of_silently_omitted():
    early = event(1, "06:00", "07:00", EventCategory.WORK, title="Early shift")
    result = planner.what_if_bedtime(
        user(events=[early]), at(0, "20:00"), at(0, "22:30"), at(1, "07:30"),
    )
    assert any("overlaps Early shift" in warning for warning in result.warnings)
    assert not result.events


def test_what_if_rejects_retrospective_and_reversed_sleep_windows():
    now = at(0, "20:00")
    with pytest.raises(ValueError, match="before now"):
        planner.what_if_bedtime(user(), now, now - timedelta(minutes=1), at(1, "07:30"))
    with pytest.raises(ValueError, match="before bedtime"):
        planner.what_if_bedtime(user(), now, at(0, "23:00"), at(0, "22:00"))


@pytest.mark.parametrize("mode", list(Mode))
def test_teen_duration_guidance_is_age_appropriate_in_every_mode(mode):
    tips = planner.snapshot(user(age_band=AgeBand.TEEN, mode=mode), at(0, "12:00")).tips
    assert any("8-10 hours" in tip for tip in tips)
    assert not any("at least 7 hours" in tip for tip in tips)


@pytest.mark.parametrize("band", [AgeBand.YOUNG_ADULT, AgeBand.ADULT, AgeBand.OLDER])
def test_adult_normal_duration_tip_remains_available(band):
    tips = planner.snapshot(user(age_band=band), at(0, "12:00")).tips
    assert any("at least 7 hours" in tip for tip in tips)


@pytest.mark.parametrize("evening", ["2026-03-28T18:00:00", "2026-10-24T18:00:00"])
def test_bedtime_target_uses_elapsed_sleep_across_clock_changes(evening):
    from datetime import datetime, time
    from app.time_utils import hours_between
    data = user([], age_band=AgeBand.ADULT, chronotype="early", usual_bedtime=time(22), usual_wake=time(6))
    plan = planner.recommend_bedtime(data, datetime.fromisoformat(evening))
    assert plan.feasible
    assert plan.sleep_h == pytest.approx(hours_between(plan.bedtime, plan.alarm) * 0.9, abs=0.01)
    assert 8 <= plan.sleep_h <= 8.1
