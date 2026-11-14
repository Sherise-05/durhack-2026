import math
from datetime import datetime, timezone

import pytest
from factories import at, nap, night, rested_nights, short_last_night, user

from app.schemas import AgeBand, CalendarEvent, Mode, SleepSession, TravelPlan
from app.services import sleep_model as sm


def test_pressure_follows_two_process_time_constants():
    assert sm.pressure_awake(0.0, sm.TAU_RISE_H) == pytest.approx(1 - math.exp(-1))
    assert sm.pressure_asleep(1.0, sm.TAU_DECAY_H) == pytest.approx(math.exp(-1))
    # restless sleep (low efficiency) drains less pressure
    assert sm.pressure_asleep(0.8, 2, efficiency=0.5) > sm.pressure_asleep(0.8, 2)


def test_daily_curve_has_afternoon_dip_and_evening_second_wind():
    model = sm.PersonModel(user(), at(0, "12:00"))
    pts = {p.at.hour + p.at.minute / 60: p.alertness for p in model.curve(at(0, "07:30"), at(0, "23:30"))}
    morning_peak = max(v for h, v in pts.items() if 8.5 <= h <= 11.5)
    dip_time, dip = min(((h, v) for h, v in pts.items() if 12 <= h <= 16), key=lambda x: x[1])
    evening_peak = max(v for h, v in pts.items() if 16 <= h <= 21)
    assert 13 <= dip_time <= 15  # Brief §4: the dip falls ~1-3 pm
    assert morning_peak - dip >= 5 and evening_peak - dip >= 5
    assert pts[23.5] < dip - 15  # sleepy by bedtime


def test_short_night_costs_alertness():
    now = at(0, "11:00")
    rested = sm.PersonModel(user(), now).alertness_now
    short = sm.PersonModel(user(short_last_night()), now).alertness_now
    assert short < rested - 8


def test_curve_is_empty_while_asleep():
    model = sm.PersonModel(user(), at(0, "12:00"))
    tonight = sm.SleepBlock(at(0, "23:00"), at(1, "07:00"))
    pts = model.curve(at(0, "22:00"), at(1, "01:00"), extra=[tonight])
    assert pts[0].alertness is not None
    assert all(p.alertness is None for p in pts if p.at > at(0, "23:00"))


def test_deep_sleep_risk_explains_the_nap_lengths():
    p = 0.5
    assert sm.deep_sleep_chance(10, p) < 0.2
    assert sm.deep_sleep_chance(20, p) < 0.4
    assert sm.deep_sleep_chance(40, p) > 0.9  # the 30-60 min danger zone
    assert sm.deep_sleep_chance(90, p) < 0.2  # end of a cycle: light sleep again
    assert sm.deep_sleep_chance(25, 0.8) > sm.deep_sleep_chance(25, 0.3)  # tired -> deep sleep sooner


def test_sleep_debt_is_a_running_total_that_naps_reduce():
    six_hour_nights = [night(-k, "23:00", "07:00", eff=0.75) for k in range(5, 0, -1)]
    now = at(0, "12:00")
    assert sm.sleep_debt_h(six_hour_nights, 8.0, now) == pytest.approx(10.0)
    one_hour_nap = nap(-1, "14:00", 60, eff=1.0)
    assert sm.sleep_debt_h(six_hour_nights + [one_hour_nap], 8.0, now) == pytest.approx(9.0)
    long_nights = [night(-k, "21:00", "07:00") for k in range(5, 0, -1)]
    assert sm.sleep_debt_h(long_nights, 8.0, now) == 0.0  # never negative


def test_hrv_is_compared_with_your_own_7_day_baseline():
    sessions = [night(-k, hrv=60) for k in range(8, 1, -1)] + [night(-1, hrv=48)]
    z, pct = sm.hrv_vs_baseline(sessions, at(0, "12:00"))
    assert pct == pytest.approx(-20.0, abs=0.5)
    assert z < -2
    assert sm.hrv_vs_baseline(sessions[:3], at(0, "12:00")) is None  # not enough baseline yet


def test_sleep_stages_are_display_only():
    now = at(0, "15:00")
    staged = user([night(-k, deep_pct=0.05, rem_pct=0.4, awakenings=12) for k in range(8, 0, -1)])
    assert sm.PersonModel(staged, now).alertness_now == sm.PersonModel(user(), now).alertness_now
    base = dict(start=at(-1, "23:00"), end=at(0, "07:00"))
    assert sm.sleep_score(SleepSession(**base, deep_pct=0.05), 8.0) == sm.sleep_score(SleepSession(**base), 8.0)
    assert sm.sleep_score(SleepSession(**base, efficiency=0.7), 8.0) < sm.sleep_score(SleepSession(**base), 8.0)


def test_gender_never_changes_the_numbers():
    now = at(0, "14:00")
    a, b = sm.PersonModel(user(gender="woman"), now), sm.PersonModel(user(gender="non-binary"), now)
    assert (a.alertness_now, a.need_h, a.debt_h) == (b.alertness_now, b.need_h, b.debt_h)


def test_aware_datetimes_become_naive_local():
    e = CalendarEvent(
        title="Lecture",
        start=datetime(2026, 11, 16, 9, tzinfo=timezone.utc),
        end=datetime(2026, 11, 16, 10, tzinfo=timezone.utc),
    )
    assert e.start.tzinfo is None and e.end.tzinfo is None


def test_social_jet_lag_compares_free_and_work_days():
    # Monday is day 0: nights -3 and -2 end on Saturday and Sunday mornings
    sessions = [
        night(-k, "01:00", "10:00") if k in (2, 3) else night(-k, "23:00", "07:00") for k in range(7, 0, -1)
    ]
    assert sm.social_jet_lag_h(user(sessions), at(0, "12:00")) == pytest.approx(2.5)


def test_gp_card_after_two_weeks_of_very_poor_sleep():
    now = at(0, "12:00")
    poor = [night(-k, rating=1) for k in range(14, 0, -1)]
    assert sm.gp_card(user(poor), now) is not None
    one_good = [night(-k, rating=4 if k == 3 else 1) for k in range(14, 0, -1)]
    assert sm.gp_card(user(one_good), now) is None
    card = sm.gp_card(user(poor, age_band=AgeBand.OLDER), now)
    assert any("NIA" in p for p in card.points)


def test_jet_lag_time_zones_and_recovery_rates():
    tokyo = TravelPlan(destination_tz="Asia/Tokyo", departure=at(3, "11:00"), arrival=at(4, "10:00"))
    new_york = TravelPlan(destination_tz="America/New_York", departure=at(3, "11:00"), arrival=at(3, "14:00"))
    assert sm.time_zone_shift_h(tokyo) == 9
    assert sm.time_zone_shift_h(new_york) == -5
    # after the 3 h pre-trip shift: 0.75 h/day east, 1 h/day west (Brief §6)
    assert sm.jet_lag_remaining_h(tokyo, AgeBand.ADULT, at(5, "10:00")) == pytest.approx(9 - 3 - 0.75)
    assert sm.jet_lag_remaining_h(new_york, AgeBand.ADULT, at(4, "14:00")) == pytest.approx(5 - 3 - 1)
    assert sm.jet_lag_remaining_h(tokyo, AgeBand.OLDER, at(5, "10:00")) > 9 - 3 - 0.75


def test_body_clock_runs_on_home_time_after_flying_east():
    tokyo = TravelPlan(destination_tz="Asia/Tokyo", departure=at(-2, "11:00"), arrival=at(-1, "10:00"))
    data = user(mode=Mode.TRAVEL, travel=tokyo)
    model = sm.PersonModel(data, at(0, "12:00"))
    settled = sm.PersonModel(user(), at(0, "12:00"))
    lag = (model.phys.mid_sleep_h - settled.phys.mid_sleep_h) % 24
    assert 4 < lag < 6  # still ~5 h behind Tokyo time a day after landing


def test_new_user_without_data_still_gets_a_curve():
    model = sm.PersonModel(user([]), at(0, "15:00"))
    assert model.alertness_now is not None and 30 < model.alertness_now < 100


def test_rested_week_has_no_debt_and_good_readiness():
    model = sm.PersonModel(user(rested_nights()), at(0, "12:00"))
    assert model.debt_h < 0.5
    assert sm.readiness(model.last_night, model.need_h, model.debt_h, 0.0) >= 80
