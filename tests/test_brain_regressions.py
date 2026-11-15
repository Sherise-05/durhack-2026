"""Regression cases from the brain review."""
from datetime import timedelta
from factories import at, event, night, nap, user
from app.schemas import CalendarEvent, EventCategory, Mode, SleepSession, TravelPlan
from app.services import modes, planner, sleep_model as sm


def test_bedtime_does_not_overlap_a_later_commitment():
    shift = CalendarEvent(title='Late shift', start=at(0, '23:30'), end=at(1, '02:00'))
    b = planner.recommend_bedtime(user(events=[shift]), at(0, '18:00'))
    assert not (shift.start < b.alarm and shift.end > b.bedtime)


def test_moving_bedtime_rechecks_calendar():
    events = [event(0, '20:00', '23:00', title='A'),
              CalendarEvent(title='B', start=at(0, '23:10'), end=at(1, '01:00'))]
    b = planner.recommend_bedtime(user(events=events), at(0, '18:00'))
    assert all(not (e.start <= b.bedtime < e.end) for e in events)


def test_alarm_accounts_for_later_event_travel():
    events = [event(1, '09:00', '10:00', title='Remote'),
              event(1, '09:30', '10:30', title='Distant', travel_min=150)]
    b = planner.recommend_bedtime(user(events=events), at(0, '18:00'))
    assert b.alarm <= events[1].start - timedelta(minutes=195)


def test_three_am_flight_is_not_ignored():
    flight = event(1, '03:00', '04:00', EventCategory.TRAVEL, title='Flight')
    b = planner.recommend_bedtime(user(events=[flight]), at(0, '18:00'))
    assert b.alarm <= flight.start - timedelta(minutes=45)


def test_jet_plan_retains_completed_preparation():
    travel = TravelPlan(destination_tz='Asia/Tokyo', departure=at(3, '11:00'), arrival=at(4, '10:00'))
    data = user(mode=Mode.TRAVEL, travel=travel)
    first = modes.jet_lag_plan(data, at(0, '12:00').date())
    later = modes.jet_lag_plan(data, at(2, '12:00').date())
    assert first.days_to_adjust == later.days_to_adjust


def test_afternoon_nap_reduces_existing_debt():
    sessions = [night(-1, '01:00', '07:00', eff=1)]
    assert sm.sleep_debt_h(sessions + [nap(0, '14:00', 60, eff=1)], 8, at(0, '16:00')) == 1


def test_duplicate_log_does_not_erase_debt():
    sessions = [night(-1, '01:00', '07:00', eff=1)]
    assert sm.sleep_debt_h(sessions + sessions, 8, at(0, '12:00')) == 2


def test_current_sleep_state_agrees_with_curve():
    data = user([SleepSession(start=at(-1, '23:00'), end=at(0, '07:00'))])
    model = sm.PersonModel(data, at(0, '03:00'))
    assert model.state_at(at(0, '04:00')).asleep
    assert model.curve(at(0, '04:00'), at(0, '04:00'))[0].alertness is None


import pytest
from datetime import datetime, time
from zoneinfo import ZoneInfo
from pydantic import ValidationError
from app.schemas import AgeBand, UserProfile
from app.services import learning, nap_recc


@pytest.mark.parametrize('rating', [1, 2])
def test_poor_checkins_cannot_lower_need(rating):
    data = user()
    sessions = [night(-k, '02:00', '07:00', eff=1, rating=rating) for k in range(7, 0, -1)]
    assert learning.sleep_need_belief(data.profile, sessions).mean >= learning.sleep_need_belief(data.profile, []).mean


def test_neutral_checkins_provide_no_need_evidence():
    sessions = [night(-k, rating=3) for k in range(7, 0, -1)]
    assert learning.sleep_need_belief(user().profile, sessions) == learning.sleep_need_belief(user().profile, [])


def test_duplicate_update_counts_once_in_learning_and_stats():
    original = night(-1, rating=1)
    updated = original.model_copy(update={'rating': 4})
    data = user([original, updated])
    assert len(data.sessions) == 1 and data.sessions[0].rating == 4
    assert learning.personalise(data, at(0, '12:00')).need.n == 1
    assert len(sm.nightly_stats(data.sessions, 8, at(0, '12:00'))) == 1


def test_ambiguous_overlapping_sleep_is_rejected():
    with pytest.raises(ValidationError, match='overlap'):
        user([night(-1), nap(0, '07:00', 60)])


def test_unsupported_sleep_duration_is_rejected():
    with pytest.raises(ValidationError, match='24 hours'):
        SleepSession(start=at(-200, '00:00'), end=at(-1, '00:00'))


def test_aware_arrival_keeps_destination_wall_clock():
    travel = TravelPlan(
        destination_tz='Asia/Tokyo',
        departure=datetime(2026, 11, 19, 11, tzinfo=ZoneInfo('Europe/London')),
        arrival=datetime(2026, 11, 20, 10, tzinfo=ZoneInfo('Asia/Tokyo')),
    )
    assert travel.arrival == datetime(2026, 11, 20, 10)
    assert travel.departure == datetime(2026, 11, 19, 11)


def test_invalid_travel_zone_is_rejected():
    with pytest.raises(ValidationError, match='IANA timezone'):
        TravelPlan(destination_tz='not/a/zone', departure=at(3, '11:00'), arrival=at(4, '10:00'))


def test_no_history_overnight_uses_current_usual_sleep():
    model = sm.PersonModel(user([]), at(0, '03:00'))
    assert model.state.asleep
    assert model.state_at(at(0, '04:00')).asleep
    assert nap_recc.recommend_nap(user([]), at(0, '03:00')).kind == 'none'


def test_stale_sleep_does_not_count_as_recent_debt():
    assert sm.sleep_debt_h([night(-30, '01:00', '07:00', eff=1)], 8, at(0, '16:00')) == 0


def test_clock_observations_crossing_noon_stay_near_prior():
    profile = UserProfile(name='Day sleeper', age_band=AgeBand.ADULT, usual_bedtime=time(8), usual_wake=time(16))
    sessions = [SleepSession(start=at(-2, '08:00'), end=at(-2, '16:00')),
                SleepSession(start=at(-1, '13:00'), end=at(-1, '21:00'))]
    assert 12 <= learning.body_clock_belief(profile, sessions, at(0, '12:00')).mean <= 17


def test_nap_length_matches_simulated_sleep_minutes():
    from factories import short_last_night
    data = user(short_last_night())
    now = at(0, '13:00')
    model = sm.PersonModel(data, now)
    plan = nap_recc.recommend_nap(data, now, 'very_tired', model=model)
    pressure = nap_recc.fuse_tiredness(model.state.pressure + model.phys.fatigue, nap_recc.Tiredness.VERY_TIRED) - model.phys.fatigue
    latency = nap_recc.time_to_fall_asleep_min(pressure, model.personal.latency_min)
    actual = ((plan.alarm - now).total_seconds() / 60 - latency) * nap_recc.NAP_EFFICIENCY
    assert actual == pytest.approx(plan.sleep_min, abs=1e-6)


def test_calendar_slot_is_not_in_the_past_or_after_window():
    from factories import short_last_night
    now = at(0, '13:00') + timedelta(seconds=45)
    slot = nap_recc.best_nap_slot(user(short_last_night()), now)
    assert slot is None or now <= slot.lie_down < at(0, '15:00')
    assert nap_recc.best_nap_slot(user(short_last_night()), at(0, '16:00')) is None


def test_nap_event_weight_is_independent_of_input_order():
    events = [event(0, '13:00', '14:00', EventCategory.SOCIAL),
              event(0, '13:00', '14:00', EventCategory.EXAM)]
    weights = modes.MODES[Mode.EXAM].event_weights
    assert nap_recc._weight(events, at(0, '13:30'), weights) == nap_recc._weight(events[::-1], at(0, '13:30'), weights) == weights[EventCategory.EXAM]


def test_empty_time_interval_has_no_points():
    assert sm.time_grid(at(0, '16:00'), at(0, '15:00')) == []
    with pytest.raises(ValueError, match='positive'):
        sm.time_grid(at(0, '15:00'), at(0, '16:00'), 0)
