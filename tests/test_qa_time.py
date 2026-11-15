"""Temporal and boundary-validation regressions from the independent QA review."""
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.schemas import SleepSession, TravelPlan, UserData, UserProfile
from app.time_utils import add_elapsed, hours_between, instant


@pytest.mark.parametrize(('start', 'end', 'hours'), [
    ('2026-10-24T23:00:00+01:00', '2026-10-25T07:00:00+00:00', 9),
    ('2026-03-28T23:00:00+00:00', '2026-03-29T07:00:00+01:00', 7),
    ('2026-10-25T01:30:00+01:00', '2026-10-25T01:15:00+00:00', 0.75),
])
def test_elapsed_sleep_and_json_round_trip_keep_instants(start, end, hours):
    session = SleepSession(start=start, end=end, efficiency=1)
    assert session.hours_asleep == hours
    restored = SleepSession.model_validate_json(session.model_dump_json())
    assert restored.hours_asleep == hours
    assert instant(restored.start) == datetime.fromisoformat(start).astimezone(timezone.utc)
    assert instant(restored.end) == datetime.fromisoformat(end).astimezone(timezone.utc)


def test_repeated_hour_records_are_distinct_in_normalisation_and_storage():
    sessions = [SleepSession(start=f'2026-10-25T01:00:00{offset}',
                             end=f'2026-10-25T01:15:00{offset}', is_nap=True)
                for offset in ('+01:00', '+00:00')]
    data = UserData(profile=UserProfile(name='Test'), sessions=sessions)
    restored = UserData.model_validate_json(data.model_dump_json())
    assert len(restored.sessions) == 2
    assert hours_between(restored.sessions[0].start, restored.sessions[1].start) == 1


@pytest.mark.parametrize('wake', ['2026-03-29T07:30:00+01:00', '2026-10-25T07:30:00+00:00'])
def test_duration_arithmetic_has_no_dst_hour_loss(wake):
    at = datetime.fromisoformat(wake)
    bedtime = add_elapsed(at, -timedelta(hours=9))
    assert hours_between(bedtime, at) == 9


def test_nonexistent_naive_local_input_is_rejected():
    with pytest.raises(ValidationError, match='does not exist'):
        SleepSession(start='2026-03-29T01:30:00', end='2026-03-29T03:00:00')


@pytest.mark.parametrize('field', ['usual_bedtime', 'usual_wake'])
def test_profile_clocks_reject_offsets(field):
    with pytest.raises(ValidationError, match='without a timezone'):
        UserProfile(name='Test', **{field: '07:30:00Z'})


@pytest.mark.parametrize('field', ['hrv_ms', 'latency_min', 'resting_hr'])
@pytest.mark.parametrize('value', [float('inf'), float('-inf'), float('nan')])
def test_nonfinite_measurements_are_rejected(field, value):
    with pytest.raises(ValidationError):
        SleepSession(start='2026-11-15T23:00:00', end='2026-11-16T07:00:00', **{field: value})


def test_invalid_trip_chronology_is_rejected_by_instant():
    with pytest.raises(ValidationError, match='arrival must be after departure'):
        TravelPlan(destination_tz='Asia/Tokyo', departure='2026-11-19T11:00:00',
                   arrival='2026-11-13T10:00:00')


def test_valid_westbound_arrival_can_have_earlier_wall_clock():
    trip = TravelPlan(destination_tz='America/New_York', departure='2026-11-19T10:00:00',
                      arrival='2026-11-19T09:00:00')
    restored = TravelPlan.model_validate_json(trip.model_dump_json())
    assert instant(restored.arrival, restored.destination_tz) > instant(restored.departure, restored.home_tz)
    assert restored.arrival.hour == 9 and restored.departure.hour == 10


def test_learning_does_not_read_future_record_in_repeated_hour():
    from app.services import learning
    from app.time_utils import local_datetime
    session = SleepSession(start='2026-10-25T00:00:00+01:00',
                           end='2026-10-25T01:05:00+00:00', rating=1)
    data = UserData(profile=UserProfile(name='Test'), sessions=[session])
    now = local_datetime(datetime.fromisoformat('2026-10-25T01:15:00+01:00'))
    personal = learning.personalise(data, now)
    assert personal.need.n == 0 and personal.body_clock.n == 0


def test_curve_point_json_preserves_repeated_clock_offsets():
    from app.schemas import AlertnessPoint
    from app.time_utils import local_datetime
    points = [AlertnessPoint(at=local_datetime(datetime.fromisoformat(f'2026-10-25T01:15:00{offset}')),
                             alertness=50) for offset in ('+01:00', '+00:00')]
    assert points[0].model_dump_json() != points[1].model_dump_json()
    assert '+01:00' in points[0].model_dump_json()
    assert '+00:00' in points[1].model_dump_json()


def test_exam_in_second_repeated_hour_is_still_upcoming():
    from app.schemas import CalendarEvent, EventCategory
    from app.services.modes import upcoming_exams
    from app.time_utils import local_datetime
    now = local_datetime(datetime.fromisoformat('2026-10-25T01:15:00+01:00'))
    exam = CalendarEvent(title='Exam', category=EventCategory.EXAM,
                         start='2026-10-25T01:05:00+00:00', end='2026-10-25T02:00:00+00:00')
    assert upcoming_exams([exam], now) == [exam]


def test_caffeine_cutoff_uses_elapsed_hours_across_dst():
    from app.services.planner import CAFFEINE_CUTOFF_H, caffeine_cutoffs
    from app.time_utils import local_datetime
    bedtime = local_datetime(datetime.fromisoformat('2026-10-25T01:30:00+00:00'))
    for drink, cutoff in caffeine_cutoffs(bedtime).items():
        assert hours_between(cutoff, bedtime) == pytest.approx(CAFFEINE_CUTOFF_H[drink])


@pytest.mark.parametrize('today', ['2026-03-29', '2026-10-25'])
def test_relative_demo_nights_remain_valid_on_clock_change_days(today):
    from datetime import date
    from app.services.personas import load_personas
    from app.services import planner
    personas = load_personas(date.fromisoformat(today))
    assert len(personas) == 4
    now = datetime.fromisoformat(f'{today}T14:00:00')
    for data in personas.values():
        assert planner.snapshot(data, now).model_dump_json()
        assert planner.day_curve(data, now)
