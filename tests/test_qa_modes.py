"""Regressions for phase-dependent light advice and actual alarm progression."""

from datetime import time

import pytest
from factories import at, night, user

from app.schemas import Mode, TravelPlan
from app.services import modes


def travel_data(destination: str):
    travel = TravelPlan(
        destination_tz=destination,
        departure=at(3, "11:00"),
        arrival=at(4, "10:00"),
    )
    return user(mode=Mode.TRAVEL, travel=travel)


@pytest.mark.parametrize("destination", ["Asia/Tokyo", "America/Los_Angeles"])
def test_large_residual_lag_has_no_precise_light_window(destination):
    plan = modes.jet_lag_plan(travel_data(destination), at(0, "12:00").date())
    large_lag = [d for d in plan.days if d.where == "destination" and d.body_clock_off_h >= 3]
    assert large_lag
    assert all(d.light is None and d.seek_light is None for d in large_lag)
    assert all("personalised light timing" in d.note for d in large_lag)
    assert not any("Get morning light after flying east" in tip for tip in plan.tips)


def test_even_small_residual_lag_has_no_precise_destination_light_window():
    plan = modes.jet_lag_plan(travel_data("Asia/Tokyo"), at(0, "12:00").date())
    small_lag = [d for d in plan.days if d.where == "destination" and 0 < d.body_clock_off_h < 3]
    assert small_lag
    assert all(d.light is None and d.seek_light is None for d in small_lag)
    assert all("personalised light timing" in d.note for d in small_lag)
    settled = plan.days[-1]
    assert settled.body_clock_off_h == 0 and settled.seek_light is None


def test_first_holiday_alarm_advances_observed_wake_by_thirty_minutes():
    data = user(
        [night(-k, "01:30", "10:30") for k in range(7, 0, -1)],
        mode=Mode.HOLIDAY,
        term_starts=at(5, "00:00").date(),
    )
    plan = modes.back_to_term_plan(data, at(0, "12:00"))
    assert plan.days[0].day == at(0, "00:00").date()
    assert plan.days[0].wake == time(10)
    assert plan.shift_h == 3
    assert plan.days[-1].wake == time(8)  # five available nights cannot complete six steps
    assert "Start today" in plan.summary
    assert "You're on 07:30" not in plan.summary


def test_holiday_final_step_never_overshoots_term_wake():
    data = user(
        [night(-k, "00:30", "08:45") for k in range(7, 0, -1)],
        mode=Mode.HOLIDAY,
        term_starts=at(5, "00:00").date(),
    )
    plan = modes.back_to_term_plan(data, at(0, "12:00"))
    assert [d.wake for d in plan.days] == [time(8, 15), time(7, 45), time(7, 30)]


def test_holiday_tip_scopes_recovery_sleep_claim():
    tip = next(tip for tip in modes.MODES[Mode.HOLIDAY].tips if "Depner" in tip)
    assert "may not undo all the effects" in tip
    assert "don't repay" not in tip
