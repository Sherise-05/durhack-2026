from datetime import date, time, timedelta

import pytest
from factories import at, event, night, user

from app.schemas import AgeBand, Chronotype, EventCategory, LifeStage, Mode
from app.services import planner
from app.services.modes import effective_mode
from app.services.personas import load_personas


def test_alarm_fits_tomorrows_first_event():
    lecture = event(1, "09:00", "10:00", EventCategory.LECTURE, title="Maths", travel_min=15)
    b = planner.recommend_bedtime(user(events=[lecture], morning_routine_min=45), at(0, "20:00"))
    assert b.alarm == at(1, "07:30")  # usual wake comes before 09:00 - 45 - 15; no lie-ins in normal mode
    early = event(1, "08:00", "09:00", EventCategory.LECTURE, title="Early lab", travel_min=15)
    b = planner.recommend_bedtime(user(events=[early], morning_routine_min=45), at(0, "20:00"))
    assert b.alarm == at(1, "07:00")
    assert b.sleep_h >= 7.9 and b.first_event == "Early lab"


def test_wind_down_dims_screens():
    b = planner.recommend_bedtime(user(), at(0, "20:00"))
    assert b.wind_down == b.bedtime - timedelta(minutes=45)
    assert any("screens down or dimmed" in r for r in b.reasons)


def test_exam_mode_switches_itself_on_two_weeks_out():
    exam = event(10, "09:00", "11:00", EventCategory.EXAM, title="Finals")
    assert effective_mode(user(events=[exam]), at(0, "12:00"))[0] == Mode.EXAM
    far = event(20, "09:00", "11:00", EventCategory.EXAM, title="Finals")
    assert effective_mode(user(events=[far]), at(0, "12:00"))[0] == Mode.NORMAL


def test_exam_mode_keeps_one_wake_time_every_day():
    exam = event(5, "09:00", "11:00", EventCategory.EXAM, travel_min=15)
    data = user(events=[exam], usual_wake=at(0, "08:30").time(), morning_routine_min=45)
    for day in range(0, 3):  # no events on these mornings, same alarm anyway
        b = planner.recommend_bedtime(data, at(day, "21:00"))
        assert b.alarm.time() == at(0, "08:00").time()  # 09:00 - 45 min routine - 15 min travel


def test_exam_morning_alarm_includes_grogginess_window():
    exam = event(1, "09:30", "12:00", EventCategory.EXAM, travel_min=15)
    late_riser = user(events=[exam], morning_routine_min=10, usual_wake=time(9, 30))
    b = planner.recommend_bedtime(late_riser, at(0, "21:00"))
    assert b.alarm == at(1, "08:55")  # 20 min grogginess (not the 10 min routine) + 15 min travel


def test_never_quietly_plans_a_short_night():
    flight = event(1, "06:00", "07:00", EventCategory.TRAVEL, title="Flight")
    b = planner.recommend_bedtime(user(events=[flight]), at(0, "23:30"))
    assert b.sleep_h < 7 and any("minimum" in w for w in b.warnings)


def test_plans_at_least_the_minimum_when_time_allows():
    for band in AgeBand:
        b = planner.recommend_bedtime(user(age_band=band), at(0, "19:00"))
        assert b.sleep_h >= (8 if band == AgeBand.TEEN else 7)


def test_sleep_debt_adds_back_up_to_an_hour():
    short_week = [night(-k, "22:00", "05:30") for k in range(8, 0, -1)]  # 6h 45m asleep a night
    rested = planner.recommend_bedtime(user(), at(0, "19:00"))
    owed = planner.recommend_bedtime(user(short_week), at(0, "19:00"))
    assert owed.debt_h > 8
    assert 0.5 < owed.sleep_h - rested.sleep_h <= 1.05


def test_body_clock_trims_catch_up_but_never_below_the_minimum():
    late_owl = [night(-k, "02:00", "08:30", eff=0.85) for k in range(8, 0, -1)]
    b = planner.recommend_bedtime(user(late_owl, chronotype=Chronotype.LATE), at(0, "19:00"))
    assert any("body clock" in r for r in b.reasons + b.warnings)
    assert b.sleep_h >= 7


def test_flags_hard_exercise_within_an_hour_of_bed():
    gym = event(0, "21:30", "22:45", EventCategory.EXERCISE, title="HIIT")
    b = planner.recommend_bedtime(user(events=[gym]), at(0, "18:00"))
    assert any("HIIT" in w for w in b.warnings)


def test_lights_out_waits_for_a_late_event_and_says_what_it_costs():
    quiz = event(0, "20:00", "23:45", EventCategory.SOCIAL, title="Pub quiz", travel_min=10)
    early = event(1, "07:30", "08:30", EventCategory.WORK, title="Shift", travel_min=20)
    b = planner.recommend_bedtime(user(events=[quiz, early]), at(0, "18:00"))
    assert b.bedtime == at(1, "00:10")  # 23:45 + 10 min home + 15 min to settle
    assert any("Pub quiz runs until" in r for r in b.reasons)
    assert any("drinks" in r for r in b.reasons)
    assert any("minimum" in w for w in b.warnings)  # 06:25 alarm leaves under 7 h


def test_past_2am_before_an_exam_says_go_to_bed_now():
    exam = event(1, "09:30", "12:00", EventCategory.EXAM)
    b = planner.recommend_bedtime(user(events=[exam]), at(1, "02:30"))
    assert b.bedtime >= at(1, "02:40")
    assert any("2 a.m." in w for w in b.warnings)


def test_what_if_refuses_to_bless_an_all_nighter():
    exam = event(1, "09:30", "12:00", EventCategory.EXAM, title="Exam")
    data = user(events=[exam])
    allnighter = planner.what_if_bedtime(data, at(0, "21:00"), at(1, "08:00"), at(1, "08:00"))
    good = planner.what_if_bedtime(data, at(0, "21:00"), at(0, "23:15"), at(1, "08:00"))
    assert any("all-nighter" in w for w in allnighter.warnings)
    assert not good.warnings
    assert good.events[0].alertness > allnighter.events[0].alertness + 15


def test_life_stage_toggles_change_tips_not_numbers():
    now = at(0, "14:00")
    plain = planner.snapshot(user(), now)
    toggled = planner.snapshot(user(life_stages=[LifeStage.CYCLE, LifeStage.MENOPAUSE]), now)
    assert len(toggled.tips) == len(plain.tips) + 2
    assert (toggled.readiness, toggled.alertness_now, toggled.sleep_need_h) == (
        plain.readiness, plain.alertness_now, plain.sleep_need_h,
    )


@pytest.mark.parametrize("key", ["maya", "leo", "priya", "june"])
def test_every_persona_produces_a_full_dashboard(key):
    today = date(2026, 11, 15)
    data = load_personas(today)[key]
    now = at(0, "14:00").replace(year=today.year, month=today.month, day=today.day)
    snap = planner.snapshot(data, now)
    assert 0 <= snap.readiness <= 100 and snap.last_night is not None
    curve = planner.day_curve(data, now)
    assert any(p.alertness is None for p in curve)  # tonight's sleep shows as a gap
    bed = planner.recommend_bedtime(data, now)
    assert bed.alarm > bed.bedtime
    snap.model_dump_json(), bed.model_dump_json()  # JSON-ready for the pages


def test_maya_is_in_exam_mode_with_real_sleep_debt():
    today = date(2026, 11, 15)
    data = load_personas(today)["maya"]
    snap = planner.snapshot(data, at(0, "14:00").replace(day=15))
    assert snap.mode == Mode.EXAM and snap.sleep_debt_h > 4 and snap.hrv_vs_baseline_pct < -10
