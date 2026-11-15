"""Acceptance regressions from the independent QA review."""
from datetime import datetime, time
import pytest
from factories import at, event, nap, night, rested_nights, user
from app.schemas import CalendarEvent, EventCategory, Mode, SleepSession
from app.services import modes, planner, sleep_model as sm


def test_dashboard_handles_no_feasible_sleep():
    shift = CalendarEvent(title='Night shift', start=at(0, '23:00'), end=at(1, '09:00'))
    assert planner.day_curve(user(events=[shift]), at(0, '20:00'))


@pytest.mark.parametrize(('start', 'end', 'hours'), [
    ('2026-10-24T23:00:00+01:00', '2026-10-25T07:00:00+00:00', 9),
    ('2026-03-28T23:00:00+00:00', '2026-03-29T07:00:00+01:00', 7),
])
def test_elapsed_sleep_survives_dst(start, end, hours):
    assert SleepSession(start=start, end=end, efficiency=1).hours_asleep == hours


def test_day_chart_keeps_morning_after_nap():
    data = user(rested_nights() + [nap(0, '14:00', 20)])
    assert planner.day_curve(data, at(0, '16:00'))[0].at == at(0, '07:30')


def test_seven_days_is_not_two_weeks():
    sessions = []
    for k in range(7, 0, -1):
        sessions += [SleepSession(start=at(-k,'22:00'), end=at(1-k,'01:00'), rating=1),
                     SleepSession(start=at(1-k,'02:00'), end=at(1-k,'07:00'), rating=1)]
    assert sm.gp_card(user(sessions), at(0,'12:00')) is None


def test_short_exam_gets_a_forecast():
    oral = event(1, '09:02', '09:12', EventCategory.EXAM, title='Oral exam')
    result = planner.what_if_bedtime(user(events=[oral]), at(0, '20:00'), at(0, '22:30'), at(1, '07:30'))
    assert any(e.title == 'Oral exam' for e in result.events)


def test_sleeping_into_work_gets_a_warning():
    work = event(1, '07:00', '09:00', EventCategory.WORK, title='Work')
    result = planner.what_if_bedtime(user(events=[work]), at(0, '20:00'), at(0, '22:30'), at(1, '07:30'))
    assert any('Work' in message for message in result.warnings)


def test_inferred_sleep_does_not_discard_real_nap():
    model = sm.PersonModel(user([nap(0, '06:00', 60)]), at(0, '12:00'))
    assert model.state.boost > 0


def test_what_if_alarm_overrides_future_part_of_ongoing_sleep():
    data = user([SleepSession(start=at(-1, '23:00'), end=at(0, '07:00'))])
    result = planner.what_if_bedtime(data, at(0, '03:00'), at(0, '03:10'), at(0, '05:00'))
    assert result.curve[0].alertness is not None


def test_first_holiday_alarm_matches_thirty_minute_promise():
    data = user([night(-k, '01:30', '10:30') for k in range(7, 0, -1)],
                mode=Mode.HOLIDAY, term_starts=at(5, '00:00').date())
    first = modes.back_to_term_plan(data, at(0, '12:00')).days[0]
    assert first.day == at(0, '00:00').date()
    assert first.wake == time(10, 0)
