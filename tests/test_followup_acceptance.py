"""Cross-feature acceptance regressions from the follow-up QA review."""
from datetime import datetime, time
import pytest
from factories import at, event, night, short_last_night, user
from app.schemas import AgeBand, EventCategory, Mode, SleepSession, TravelPlan
from app.services import learning, modes, planner, sleep_model as sm


def test_trip_departure_is_a_wake_deadline_without_duplicate_event():
    travel = TravelPlan(destination_tz='Asia/Tokyo', departure=at(3,'03:00'), arrival=at(3,'17:00'))
    data = user(mode=Mode.TRAVEL, travel=travel, usual_wake=time(7))
    result = planner.recommend_bedtime(data, at(2,'18:00'))
    assert result.alarm < travel.departure


def test_generic_light_tip_does_not_bypass_jetlag_precaution():
    travel = TravelPlan(destination_tz='Asia/Tokyo', departure=at(-2,'11:00'), arrival=at(-1,'10:00'))
    data = user(short_last_night(), mode=Mode.TRAVEL, travel=travel)
    result = planner.recommend_bedtime(data, at(0,'14:00'))
    assert not any('Get daylight soon after waking to shift it earlier' in text for text in result.warnings)


def test_holiday_plan_does_not_repeat_a_completed_step():
    base = [night(-k,'01:30','10:30') for k in range(7,0,-1)]
    first = user(base, mode=Mode.HOLIDAY, term_starts=at(6,'00:00').date())
    next_day = user(base + [night(0,'01:00','10:00')], mode=Mode.HOLIDAY, term_starts=at(6,'00:00').date())
    old_plan = modes.back_to_term_plan(first, at(0,'12:00'))
    updated_plan = modes.back_to_term_plan(next_day, at(1,'12:00'))
    assert updated_plan.days[0].wake == old_plan.days[1].wake


def split_and_whole():
    whole, split = [], []
    for k in range(7,0,-1):
        whole.append(night(-k,'23:00','07:00',eff=1,rating=4))
        split += [SleepSession(start=at(-k,'23:00'),end=at(1-k,'03:00'),efficiency=1),
                  SleepSession(start=at(1-k,'03:00'),end=at(1-k,'07:00'),efficiency=1,rating=4)]
    return user(whole), user(split)


def test_splitting_same_sleep_does_not_change_readiness():
    whole, split = split_and_whole()
    assert planner.snapshot(whole,at(0,'12:00')).readiness == planner.snapshot(split,at(0,'12:00')).readiness


def test_final_morning_rating_uses_whole_sleep_not_last_fragment():
    whole, split = split_and_whole()
    assert learning.personalise(whole,at(0,'12:00')).need.mean == learning.personalise(split,at(0,'12:00')).need.mean


def test_repeated_hour_midpoint_uses_elapsed_instants():
    session = SleepSession(start='2026-10-25T01:30:00+01:00',end='2026-10-25T01:15:00+00:00',efficiency=1)
    clock_mid = (learning.mid_sleep_since_noon(session.start,session.end)+12) % 24
    # Actual midpoint is 01:52:30 BST, approximately 1.875 clock hours.
    assert clock_mid == pytest.approx(1.875, abs=1/60)


def test_completed_event_does_not_prevent_sleep_for_rest_of_night():
    finished = event(0,'00:15','00:45',EventCategory.OTHER,title='Finished call')
    result = planner.recommend_bedtime(user(events=[finished]),at(0,'02:00'))
    assert result.feasible and result.alarm > at(0,'02:00')


def test_generated_dst_alarm_matches_its_serialized_clock():
    result = planner.recommend_bedtime(user([],usual_wake=time(1,30)),datetime(2026,3,28,18))
    serialized = datetime.fromisoformat(result.model_dump(mode='json')['alarm'])
    assert result.alarm.hour == serialized.hour and result.alarm.minute == serialized.minute


def test_ongoing_night_counts_already_slept_hours_toward_minimum():
    data = user([SleepSession(start=at(-1,'23:00'),end=at(0,'07:00'),efficiency=1)])
    result = planner.recommend_bedtime(data,at(0,'03:00'))
    assert not any('under the 7 h minimum' in warning for warning in result.warnings)


def test_future_sessions_do_not_change_todays_standalone_plan():
    travel = TravelPlan(destination_tz='Asia/Tokyo',departure=at(3,'11:00'),arrival=at(4,'10:00'))
    base = user(mode=Mode.TRAVEL,travel=travel)
    future = [night(10+k,'02:00','07:00',eff=0.9,rating=4) for k in range(14)]
    changed = user(base.sessions+future,mode=Mode.TRAVEL,travel=travel)
    today = at(0,'12:00').date()
    assert modes.jet_lag_plan(base,today).days[0].bedtime == modes.jet_lag_plan(changed,today).days[0].bedtime
