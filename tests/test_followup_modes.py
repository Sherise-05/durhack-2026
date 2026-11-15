"""Multi-day adherence and as-of evidence invariants for mode plans."""
from datetime import datetime, time

import pytest
from factories import at, night, user

from app.schemas import Mode, SleepSession, TravelPlan
from app.services import modes, sleep_model as sm


def holiday_data(sessions, term_day=6):
    return user(sessions, mode=Mode.HOLIDAY, term_starts=at(term_day, '00:00').date())


def test_followed_holiday_steps_are_not_restarted_by_older_history():
    sessions = [night(-k, '01:30', '10:30') for k in range(7, 0, -1)]
    original = modes.back_to_term_plan(holiday_data(sessions), at(0, '12:00'))
    assert [d.wake for d in original.days] == [time(10), time(9, 30), time(9), time(8, 30), time(8), time(7, 30)]

    sessions += [night(0, '01:00', '10:00')]
    next_day = modes.back_to_term_plan(holiday_data(sessions), at(1, '12:00'))
    assert next_day.days[0].wake == original.days[1].wake
    assert next_day.days[-1].wake == time(7, 30)
    assert 'Start today' not in next_day.summary

    sessions += [night(1, '00:30', '09:30')]
    day_two = modes.back_to_term_plan(holiday_data(sessions), at(2, '12:00'))
    assert day_two.days[0].wake == original.days[2].wake


def test_holiday_uses_actual_adherence_rather_than_assuming_a_step_happened():
    sessions = [night(-k, '01:30', '10:30') for k in range(7, 0, -1)]
    sessions += [night(0, '01:30', '10:30')]  # user did not advance the first alarm
    plan = modes.back_to_term_plan(holiday_data(sessions), at(1, '12:00'))
    assert plan.days[0].wake == time(10)
    assert 'Start today' in plan.summary


def test_holiday_latest_wake_is_whole_episode_not_first_fragment():
    sessions = [night(-k, '01:30', '10:30') for k in range(7, 1, -1)]
    sessions += [SleepSession(start=at(0, '01:00'), end=at(0, '05:00')),
                 SleepSession(start=at(0, '05:00'), end=at(0, '10:00'))]
    plan = modes.back_to_term_plan(holiday_data(sessions), at(0, '12:00'))
    assert plan.days[0].wake == time(9, 30)


def travel_data(sessions=None):
    travel = TravelPlan(destination_tz='Asia/Tokyo', departure=at(3, '11:00'), arrival=at(4, '10:00'))
    return user(sessions, mode=Mode.TRAVEL, travel=travel)


def test_future_sessions_cannot_change_todays_standalone_travel_plan():
    data = travel_data()
    future = [night(10+k, '02:00', '07:00', eff=0.9, rating=4) for k in range(14)]
    changed = travel_data(data.sessions + future)
    today = at(0, '00:00').date()
    assert modes.jet_lag_plan(data, today) == modes.jet_lag_plan(changed, today)
    assert modes.jet_lag_plan(data, today, now=at(0, '12:00')) == modes.jet_lag_plan(changed, today, now=at(0, '12:00'))


def test_plan_default_matches_model_evidence_when_given_same_cutoff():
    sessions = [night(-k, '22:30', '07:30', eff=0.8, rating=1) for k in range(8, 0, -1)]
    data = travel_data(sessions)
    now = at(0, '12:00')
    model = sm.PersonModel(data, now)
    assert modes._in_bed_h(data, now) == pytest.approx(model.need_h / model.efficiency)


def test_date_only_travel_plan_uses_start_of_day_and_explicit_now_includes_completed_morning():
    sessions = [night(-k, '22:30', '07:30', eff=0.9) for k in range(8, 1, -1)]
    # Very different new observation exists in the supplied data but finishes at 07:30.
    sessions += [night(-1, '21:30', '07:30', eff=0.9, rating=1)]
    data = travel_data(sessions)
    today = at(0, '00:00').date()
    date_only = modes.jet_lag_plan(data, today)
    at_midnight = modes.jet_lag_plan(data, today, now=at(0, '00:00'))
    at_noon = modes.jet_lag_plan(data, today, now=at(0, '12:00'))
    assert date_only == at_midnight
    assert date_only.days[0].bedtime != at_noon.days[0].bedtime
