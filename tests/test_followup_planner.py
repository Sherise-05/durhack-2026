"""Cross-feature planner acceptance cases from the follow-up audit."""
from datetime import datetime, time, timedelta

import pytest
from factories import at, event, short_last_night, user

from app.schemas import CalendarEvent, EventCategory, Mode, SleepSession, TravelPlan
from app.services import planner
from app.time_utils import hours_between, instant


def trip(**settings):
    return TravelPlan(destination_tz="Asia/Tokyo", departure=at(3, "03:00"),
                      arrival=at(3, "17:00"), **settings)


def test_trip_departure_binds_without_duplicate_calendar_record():
    travel = trip(travel_min=30, pre_departure_buffer_min=60)
    plan = planner.recommend_bedtime(user(mode=Mode.TRAVEL, travel=travel), at(2, "18:00"))
    assert plan.alarm == at(3, "00:45")  # 45 routine + 30 ground travel + 60 airport buffer
    assert plan.first_event == "Flight departure"
    assert any("30 min ground travel" in reason and "60 min airport" in reason for reason in plan.reasons)


def test_unspecified_trip_buffers_are_not_invented_or_hidden():
    plan = planner.recommend_bedtime(user(mode=Mode.TRAVEL, travel=trip()), at(2, "18:00"))
    assert plan.alarm == at(3, "02:15")
    assert any("zero value does not reserve" in warning for warning in plan.warnings)


def test_duplicate_flight_deadlines_are_not_added_together():
    travel = trip(travel_min=30, pre_departure_buffer_min=60)
    flight = event(3, "03:00", "04:00", EventCategory.TRAVEL, title="Flight", travel_min=90)
    plan = planner.recommend_bedtime(user(mode=Mode.TRAVEL, travel=travel, events=[flight]), at(2, "18:00"))
    assert plan.alarm == at(3, "00:45")


@pytest.mark.parametrize("mode", [Mode.NORMAL, Mode.TRAVEL])
def test_travel_precaution_applies_to_all_generic_light_advice(mode):
    travel = TravelPlan(destination_tz="Asia/Tokyo", departure=at(-2, "11:00"), arrival=at(-1, "10:00"))
    data = user(short_last_night(), mode=mode, travel=travel)
    plan = planner.recommend_bedtime(data, at(0, "14:00"))
    snap = planner.snapshot(data, at(0, "14:00"))
    text = " ".join(plan.reasons + plan.warnings + snap.tips)
    assert "Get daylight soon after waking to shift it earlier" not in text
    assert "morning light moves your body clock earlier" not in text
    assert "personalised light guidance" in text or "personalised" in text


def test_completed_event_does_not_bind_remaining_sleep():
    completed = event(0, "00:15", "00:45", title="Finished call")
    plan = planner.recommend_bedtime(user(events=[completed]), at(0, "02:00"))
    assert plan.feasible and plan.alarm == at(0, "07:30")
    assert plan.first_event is None


def test_ongoing_event_retains_missed_deadline_warning():
    current = event(0, "02:00", "04:00", title="Night duty")
    plan = planner.recommend_bedtime(user(events=[current]), at(0, "03:00"))
    assert not plan.feasible
    assert plan.first_event == "Night duty"
    assert any("already passed" in warning for warning in plan.warnings)


@pytest.mark.parametrize("evening", ["2026-03-28T18:00:00", "2026-10-24T18:00:00"])
def test_derived_clocks_and_text_match_json_through_clock_changes(evening):
    result = planner.recommend_bedtime(user([], usual_wake=time(1, 30)), datetime.fromisoformat(evening))
    payload = result.model_dump(mode="json")
    for key in ("alarm", "bedtime", "wind_down"):
        actual = getattr(result, key)
        restored = datetime.fromisoformat(payload[key])
        assert (actual.hour, actual.minute) == (restored.hour, restored.minute)
        assert instant(actual) == instant(restored)
    assert result.alarm.strftime("%H:%M") in result.headline


def test_ongoing_primary_sleep_counts_observed_and_remaining_hours():
    session = SleepSession(start=at(-1, "23:00"), end=at(0, "07:00"), efficiency=1)
    data = user([session])
    result = planner.recommend_bedtime(data, at(0, "03:00"))
    assert result.in_progress and result.feasible
    assert result.bedtime == session.start and result.alarm == session.end
    assert result.sleep_h == 8 and result.remaining_sleep_h == 4
    assert not any("under the 7 h minimum" in warning for warning in result.warnings)
    assert not any("go to bed as soon" in warning for warning in result.warnings)
    assert "Sleep in progress" in result.headline
    curve = planner.day_curve(data, at(0, "03:00"))
    assert any(point.at == at(0, "05:00") and point.alertness is None for point in curve)
    assert any(point.at == at(0, "07:00") and point.alertness is not None for point in curve)


def test_ongoing_split_episode_counts_awake_gap_as_awake():
    data = user([
        SleepSession(start=at(-1, "23:00"), end=at(0, "02:00"), efficiency=1, episode_id="night"),
        SleepSession(start=at(0, "03:00"), end=at(0, "07:00"), efficiency=1, episode_id="night"),
    ])
    result = planner.recommend_bedtime(data, at(0, "04:00"))
    assert result.in_progress and result.sleep_h == 7
    assert result.remaining_sleep_h == 3


def test_ongoing_night_still_honours_early_commitment():
    current = SleepSession(start=at(-1, "23:00"), end=at(0, "07:00"), efficiency=1)
    work = event(0, "05:00", "06:00", EventCategory.WORK, title="Early shift")
    result = planner.recommend_bedtime(user([current], events=[work]), at(0, "03:00"))
    assert result.alarm == at(0, "04:15")
    assert result.sleep_h == 5.25 and result.remaining_sleep_h == 1.25
    assert any("full night" in warning.lower() and "minimum" in warning for warning in result.warnings)


def test_social_event_tip_does_not_guarantee_faster_sleep_onset():
    dinner = event(0, "19:00", "21:00", EventCategory.SOCIAL, title="Dinner")
    result = planner.recommend_bedtime(user(events=[dinner]), at(0, "18:00"))
    text = " ".join(result.reasons)
    assert "effects vary with dose" in text
    assert "alcohol helps you drop off" not in text


def test_airport_buffer_explanation_only_appears_when_flight_is_binding():
    travel = TravelPlan(destination_tz='Asia/Tokyo', departure=at(3,'11:00'), arrival=at(4,'10:00'))
    result = planner.recommend_bedtime(user(mode=Mode.TRAVEL,travel=travel),at(0,'18:00'))
    assert not any('airport/check-in buffer' in text for text in result.reasons+result.warnings)
