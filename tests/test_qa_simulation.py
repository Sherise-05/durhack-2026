"""Acceptance cases for observed, inferred and hypothetical sleep precedence."""
from datetime import datetime, timedelta

import pytest
from factories import at, nap, night, user

from app.schemas import SleepSession
from app.services import sleep_model as sm
from app.time_utils import instant, local_datetime


def test_hypothetical_alarm_replaces_future_portion_of_ongoing_record():
    data = user([SleepSession(start=at(-1, "23:00"), end=at(0, "07:00"))])
    model = sm.PersonModel(data, at(0, "03:00"))
    hypothetical = sm.SleepBlock(at(0, "03:10"), at(0, "05:00"))
    curve = model.curve(at(0, "05:00"), at(0, "07:00"), extra=[hypothetical])
    assert all(point.alertness is not None for point in curve)
    # Observing a scenario does not mutate the original recorded timeline.
    assert model.state_at(at(0, "06:00")).asleep


def test_no_more_sleep_scenario_can_end_an_ongoing_record():
    data = user([SleepSession(start=at(-1, "23:00"), end=at(0, "07:00"))])
    model = sm.PersonModel(data, at(0, "03:00"))
    curve = model.curve(at(0, "03:00"), at(0, "04:00"), replace_future=True)
    assert all(point.alertness is not None for point in curve)
    assert model.curve(at(0, "04:00"), at(0, "04:00"))[0].alertness is None


def test_hypothetical_curve_preserves_past_sleep_pressure():
    model = sm.PersonModel(user(), at(0, "12:00"))
    baseline = model.curve(at(0, "08:00"), at(0, "10:00"))
    scenario = model.curve(
        at(0, "08:00"), at(0, "10:00"),
        extra=[sm.SleepBlock(at(0, "14:00"), at(0, "14:20"), is_nap=True)],
    )
    assert scenario == baseline


def test_hypothetical_sleep_cannot_rewrite_recorded_history():
    model = sm.PersonModel(user(), at(0, "12:00"))
    with pytest.raises(ValueError, match="at or after now"):
        model.curve(at(0, "08:00"), at(0, "14:00"), extra=[sm.SleepBlock(at(0, "09:00"), at(0, "10:00"))])


def test_recorded_nap_is_not_swallowed_by_inferred_sleep():
    model = sm.PersonModel(user([nap(0, "06:00", 60)]), at(0, "12:00"))
    assert model.state.boost > 0
    assert model.state.boost_at == at(0, "07:00")
    assert any(block.is_nap and block.start == at(0, "06:00") and block.end == at(0, "07:00") for block in model.blocks)
    assert all(instant(a.end) <= instant(b.start) for a, b in zip(model.blocks, model.blocks[1:]))


def test_sleep_starting_exactly_now_is_retained_and_asleep():
    now = at(0, "13:00")
    model = sm.PersonModel(user([nap(0, "13:00", 20)]), now)
    assert model.state.asleep
    assert model.state_at(at(0, "13:10")).asleep
    assert not model.state_at(at(0, "13:20")).asleep
    assert model.curve(now, now)[0].alertness is None


def test_split_sleep_for_seven_days_does_not_claim_a_fortnight():
    sessions = []
    for k in range(7, 0, -1):
        sessions.extend([
            SleepSession(start=at(-k, "22:00"), end=at(1-k, "01:00"), rating=1),
            SleepSession(start=at(1-k, "02:00"), end=at(1-k, "07:00"), rating=1),
        ])
    assert sm.gp_card(user(sessions), at(0, "12:00")) is None


def test_fortnight_prompt_requires_consecutive_distinct_dates():
    sessions = [night(-k, rating=1) for k in range(15, 0, -1) if k != 8]
    assert len(sessions) == 14
    assert sm.gp_card(user(sessions), at(0, "12:00")) is None


def test_one_good_fragment_cancels_a_poor_night():
    sessions = [night(-k, rating=1) for k in range(14, 1, -1)]
    sessions += [
        SleepSession(start=at(-1, "22:00"), end=at(0, "01:00"), rating=1),
        SleepSession(start=at(0, "02:00"), end=at(0, "07:00"), rating=4),
    ]
    assert sm.gp_card(user(sessions), at(0, "12:00")) is None


def test_fortnight_prompt_requires_recent_checkins():
    sessions = [night(-k, rating=1) for k in range(16, 2, -1)]
    assert sm.gp_card(user(sessions), at(0, "12:00")) is None


def test_fall_back_grid_and_sleep_simulation_follow_elapsed_instants():
    start = local_datetime(datetime.fromisoformat("2026-10-25T01:30:00+01:00"))
    end = local_datetime(datetime.fromisoformat("2026-10-25T01:15:00+00:00"))
    block = sm.SleepBlock(start, end, efficiency=1)
    grid = sm.time_grid(start, end, step_min=15)
    assert len(grid) == 4
    assert all((instant(b) - instant(a)) == timedelta(minutes=15) for a, b in zip(grid, grid[1:]))
    assert block.minutes_asleep == 45
    states = sm.simulate(sm.BodyState(start, pressure=0.6), [block], grid)
    assert states[-1].pressure == pytest.approx(sm.pressure_asleep(0.6, 0.75))
    assert states[-1].woke_at == end
    assert all(0 <= state.pressure <= 1 for state in states)
