from datetime import time, timedelta

import pytest
from factories import at, night, user

from app.schemas import AgeBand, Mode, TravelPlan
from app.services import modes, planner


def traveller(destination: str, age_band=AgeBand.ADULT, hours_flying: int = 12):
    travel = TravelPlan(
        destination_tz=destination, departure=at(3, "11:00"), arrival=at(3, "11:00") + timedelta(hours=hours_flying)
    )
    return user(mode=Mode.TRAVEL, travel=travel, age_band=age_band, usual_bedtime=time(23), usual_wake=time(7))


def test_london_to_tokyo_plan():
    plan = modes.jet_lag_plan(traveller("Asia/Tokyo", hours_flying=14), at(0, "00:00").date())
    assert plan.direction == "advance" and plan.shift_h == 9
    home = [d for d in plan.days if d.where == "home"]
    assert [d.wake for d in home] == [time(6), time(5), time(4)]  # 1 h earlier per day for 3 days
    assert all(d.light == "morning" for d in home)
    away = [d for d in plan.days if d.where == "destination"]
    offs = [d.body_clock_off_h for d in away]
    assert offs[0] == 6 and offs[-1] == 0 and offs == sorted(offs, reverse=True)
    assert plan.days_to_adjust == 8  # 6 h left at 0.75 h/day: east is slower


def test_flying_west_is_faster_and_uses_evening_light():
    plan = modes.jet_lag_plan(traveller("America/New_York", hours_flying=8), at(0, "00:00").date())
    assert plan.direction == "delay" and plan.shift_h == 5
    home = [d for d in plan.days if d.where == "home"]
    assert [d.wake for d in home] == [time(8), time(9), time(10)]
    assert all(d.light == "evening" for d in plan.days if d.body_clock_off_h > 0)
    assert plan.days_to_adjust == 2  # 2 h left at 1 h/day


def test_starting_the_pre_trip_shift_late_never_jumps_3_hours():
    plan = modes.jet_lag_plan(traveller("America/New_York", hours_flying=8), at(2, "00:00").date())
    home = [d for d in plan.days if d.where == "home"]
    assert [d.wake for d in home] == [time(8)]  # one night left: shift 1 h, not 3
    assert plan.days_to_adjust == 4  # the other 4 h get done after landing


def test_small_time_differences_get_tips_only():
    plan = modes.jet_lag_plan(traveller("Europe/Helsinki", hours_flying=3), at(0, "00:00").date())
    assert plan.shift_h == 2 and plan.days == [] and plan.tips


def test_over_65s_get_a_longer_adjustment():
    adult = modes.jet_lag_plan(traveller("Asia/Tokyo"), at(0, "00:00").date())
    older = modes.jet_lag_plan(traveller("Asia/Tokyo", age_band=AgeBand.OLDER), at(0, "00:00").date())
    assert older.days_to_adjust > adult.days_to_adjust


def test_no_medication_advice():
    plan = modes.jet_lag_plan(traveller("Asia/Tokyo"), at(0, "00:00").date())
    text = " ".join([plan.summary, *plan.tips, *(d.note for d in plan.days)]).lower()
    assert "pharmacist or gp" in text and "mg" not in text


def test_tonights_bedtime_follows_the_pre_trip_plan():
    data = traveller("Asia/Tokyo", hours_flying=14)
    b = planner.recommend_bedtime(data, at(0, "20:00"))
    assert b.alarm == at(1, "06:00")


def holidaymaker(term_in_days: int):
    drifted = [night(-k, "01:30", "10:30") for k in range(7, 0, -1)]
    return user(drifted, mode=Mode.HOLIDAY, term_starts=at(term_in_days, "00:00").date())


def test_back_to_term_walks_wake_time_back_30_min_a_day():
    plan = modes.back_to_term_plan(holidaymaker(10), at(0, "12:00"))
    wakes = [d.wake.hour * 60 + d.wake.minute for d in plan.days]
    assert plan.direction == "advance" and plan.shift_h == pytest.approx(2.5)  # mid-sleep 06:00 vs 03:30
    assert all(a - b == 30 for a, b in zip(wakes, wakes[1:]))
    assert plan.days[-1].wake == time(7, 30)  # usual (term-time) wake on the first morning of term
    assert plan.days[-1].day == at(9, "00:00").date()


def test_back_to_term_warns_when_left_too_late():
    plan = modes.back_to_term_plan(holidaymaker(2), at(0, "12:00"))
    assert plan.days[-1].body_clock_off_h > 0 and "Start today" in plan.summary


def test_holiday_alarm_stays_within_an_hour_of_term_time():
    b = planner.recommend_bedtime(holidaymaker(30), at(0, "21:00"))
    assert b.alarm <= at(1, "08:30")  # term-time wake 07:30 + 1 h
