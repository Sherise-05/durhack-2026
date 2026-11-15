"""Modes: the same maths tuned for different weeks of your life.

normal   the defaults.
exam     switches itself on when an exam is in the calendar within 2 weeks (our
         choice, Brief §5). Fixed wake time every day, early enough for your
         earliest exam, so a 9 a.m. exam doesn't feel like 6 a.m. Bedtime before
         2 a.m. Never an all-nighter.
holiday  relaxed alarms, but wake time stays within ~1 h of term time (our
         choice, Brief §7). In the last days before term it walks your wake time
         back 30 min/day (our choice) instead of all at once.
travel   3+ h time difference: a 3-day pre-trip shift of 1 h/day towards the
         destination with morning/evening light reminders, then ~1 day per time
         zone after landing, slower going east. Under 3 h: tips only (Brief §6).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from statistics import median

from app.schemas import (
    CalendarEvent,
    EventCategory,
    LifeStage,
    Mode,
    ShiftDay,
    ShiftPlan,
    UserData,
    UserProfile,
)
from app.time_utils import instant
from app.services import learning
from app.services.sleep_history import primary_nights
from app.services.sleep_model import (
    JET_LAG_MIN_H,
    PRE_TRIP_H_PER_DAY,
    adjust_rate_h_per_day,
    fmt_h,
    pre_trip_nights,
    pre_trip_shift_h,
    time_zone_shift_h,
)

EXAM_LOOKAHEAD_DAYS = 14  # our choice
GROGGY_MIN = 20  # wait ~20 min after waking before an exam, a drive or a big decision (Brief §3)
EXAM_LATEST_BEDTIME = time(2, 0)  # later bedtimes linked to worse scores (Okano et al. 2019)
BACK_TO_TERM_H_PER_DAY = 0.5  # our choice
MORNING_LIGHT_H = 2.0  # first 2 h after waking (our choice)
EVENING_LIGHT_H = (4.0, 2.0)  # from 4 h to 2 h before bed (our choice)

_WEIGHTS = {
    EventCategory.EXAM: 2.0,
    EventCategory.TRAVEL: 1.5,
    EventCategory.LECTURE: 1.3,
    EventCategory.STUDY: 1.3,
    EventCategory.WORK: 1.3,
    EventCategory.EXERCISE: 1.1,
    EventCategory.SOCIAL: 1.0,
    EventCategory.OTHER: 1.0,
    EventCategory.REST: 0.5,
}


@dataclass(frozen=True)
class ModeSettings:
    mode: Mode
    label: str
    lie_in_h: float  # how much later than usual you may wake on a free morning
    wind_down_min: int
    debt_repay_fraction: float  # share of sleep debt to win back tonight
    max_extra_sleep_h: float
    latest_bedtime: time | None = None
    event_weights: dict[EventCategory, float] = field(default_factory=lambda: dict(_WEIGHTS))
    tips: tuple[str, ...] = ()


MODES: dict[Mode, ModeSettings] = {
    Mode.NORMAL: ModeSettings(
        Mode.NORMAL, "Normal", lie_in_h=0.0, wind_down_min=45, debt_repay_fraction=0.25,
        max_extra_sleep_h=1.0,
        tips=(
            "Aim for at least 7 hours most nights (AASM / Sleep Research Society).",
            "Wind-down means screens down or dimmed: an iPad before bed meant ~10 min longer to fall asleep (Chang et al.).",
            "Get daylight soon after waking: morning light moves your body clock earlier.",
        ),
    ),
    Mode.EXAM: ModeSettings(
        Mode.EXAM, "Exam mode", lie_in_h=0.0, wind_down_min=60, debt_repay_fraction=0.35,
        max_extra_sleep_h=1.0, latest_bedtime=EXAM_LATEST_BEDTIME,
        event_weights={**_WEIGHTS, EventCategory.EXAM: 3.0, EventCategory.STUDY: 1.6},
        tips=(
            "The sleep that counts is across the revision weeks, not just the night before (Okano et al. 2019).",
            "Same wake time every day: students with regular sleep had better grades (Phillips et al. 2017).",
            "Exam mode aims for bed before 2 a.m., a planning choice informed by an observational student study, not a universal cutoff.",
            "Avoid all-nighters: sleep deprivation can impair learning and memory (Yoo et al. 2007).",
        ),
    ),
    Mode.HOLIDAY: ModeSettings(
        Mode.HOLIDAY, "Holiday mode", lie_in_h=1.0, wind_down_min=30, debt_repay_fraction=0.5,
        max_extra_sleep_h=1.5,
        tips=(
            "Lie-ins are fine, but keep your alarm within about an hour of term time.",
            "Weekend catch-up sleep may not undo all the effects of repeated short nights; regular sufficient sleep matters (Depner et al. 2019).",
            "Before term starts, Doze moves your wake time back gradually, not all at once.",
        ),
    ),
    Mode.TRAVEL: ModeSettings(
        Mode.TRAVEL, "Travel mode", lie_in_h=0.0, wind_down_min=45, debt_repay_fraction=0.25,
        max_extra_sleep_h=1.0,
        tips=(
            "Flying west is easier (it lengthens your day); east is harder.",
            "Light timing depends on your current body-clock phase. While adjusting, use personalised light timing rather than a fixed local morning or evening window.",
            "Thinking about melatonin? Ask a pharmacist or GP: Doze doesn't give medication advice.",
        ),
    ),
}

LIFE_STAGE_TIPS = {  # opt-in, tips only, never numbers (Brief §11)
    LifeStage.CYCLE: "Sleep is more often disrupted in roughly the last two weeks of the cycle (PMS / PMDD). Be extra kind to your wind-down then.",
    LifeStage.PREGNANCY: "In pregnancy you may need more than 7-9 hours, and sleep problems are common, especially in the third trimester. Your midwife or GP can help.",
    LifeStage.MENOPAUSE: "Hot flushes can wake you at night. If it's affecting your days, your GP can talk through options.",
}


def life_stage_tips(profile: UserProfile) -> list[str]:
    return [LIFE_STAGE_TIPS[s] for s in profile.life_stages]


# --------------------------------------------------------------------------- exam mode


def upcoming_exams(events: list[CalendarEvent], now: datetime) -> list[CalendarEvent]:
    horizon = now + timedelta(days=EXAM_LOOKAHEAD_DAYS)
    return sorted(
        (e for e in events if e.category == EventCategory.EXAM and instant(now) <= instant(e.start) <= instant(horizon)),
        key=lambda e: instant(e.start),
    )


def effective_mode(data: UserData, now: datetime) -> tuple[Mode, str | None]:
    """The mode to plan with, plus a line for the UI explaining why."""
    p = data.profile
    exams = upcoming_exams(data.events, now)
    if p.mode in (Mode.NORMAL, Mode.EXAM) and exams:
        days = (exams[0].start.date() - now.date()).days
        when = "today" if days == 0 else "tomorrow" if days == 1 else f"in {days} days"
        return Mode.EXAM, f"Exam mode is on: {exams[0].title} is {when}."
    if p.mode == Mode.HOLIDAY and p.term_starts:
        return Mode.HOLIDAY, f"Holiday mode: term starts {p.term_starts:%a %d %b}."
    if p.mode == Mode.TRAVEL and p.travel:
        place = p.travel.destination_tz.split("/")[-1].replace("_", " ")
        return Mode.TRAVEL, f"Travel mode: flying to {place} on {p.travel.departure:%a %d %b}."
    return p.mode, None


def lead_min(profile: UserProfile, event: CalendarEvent) -> int:
    """Minutes between the alarm and the start of a morning event."""
    if event.category in (EventCategory.EXAM, EventCategory.TRAVEL):
        return max(profile.morning_routine_min, GROGGY_MIN) + event.travel_min
    return profile.morning_routine_min + event.travel_min


def exam_wake_time(data: UserData, now: datetime) -> time:
    """Exam mode's fixed wake time: early enough for your earliest exam in the next
    2 weeks (grogginess window + travel), never later than your usual wake."""
    p = data.profile
    usual = p.usual_wake.hour * 60 + p.usual_wake.minute
    needed = [
        e.start.hour * 60 + e.start.minute - lead_min(p, e)
        for e in upcoming_exams(data.events, now)
    ]
    m = max(0, min([usual, *needed]))
    return time(m // 60, m % 60)


# --------------------------------------------------------------------------- shift plans


def _shift(t: time, hours: float) -> time:
    m = round((t.hour * 60 + t.minute + hours * 60) / 5) * 5 % (24 * 60)
    return time(m // 60, m % 60)


def _light(east: bool, bed: time, wake: time) -> tuple[str, tuple[time, time]]:
    if east:
        return "morning", (wake, _shift(wake, MORNING_LIGHT_H))
    return "evening", (_shift(bed, -EVENING_LIGHT_H[0]), _shift(bed, -EVENING_LIGHT_H[1]))


def _place(tz: str) -> str:
    return tz.split("/")[-1].replace("_", " ")


def _in_bed_h(data: UserData, now: datetime) -> float:
    """Time in bed from completed primary nights available at the cutoff.

    Use the same evidence window and efficiency rule as PersonModel. Supplied
    future records must not change a plan made from today's information.
    """
    completed = primary_nights(data.sessions, now=now)
    need = learning.sleep_need_belief(data.profile, completed).mean
    effs = [s.efficiency for s in completed[-7:]]
    eff = min(0.98, max(0.75, median(effs))) if effs else 0.9
    return need / eff


def jet_lag_plan(
    data: UserData, today: date, in_bed_h: float | None = None, *, now: datetime | None = None,
) -> ShiftPlan | None:
    """Day-by-day jet-lag plan. ``in_bed_h`` sets bedtimes from wake times (the
    planner passes tonight's figure; default: learned need / usual efficiency).
    ``now`` is the evidence cutoff. Date-only callers use the start of ``today``;
    pass ``now`` to include sleeps that have completed later today.
    """
    p, travel = data.profile, data.profile.travel
    if travel is None:
        return None
    cutoff = now if now is not None else datetime.combine(today, time.min)
    in_bed = in_bed_h if in_bed_h is not None else _in_bed_h(data, cutoff)
    shift = time_zone_shift_h(travel)
    east = shift > 0
    home, away = _place(travel.home_tz), _place(travel.destination_tz)
    size = abs(shift)
    rel = f"{size:g} h {'ahead of' if east else 'behind'} {home}"
    tips = list(MODES[Mode.TRAVEL].tips)
    if size < JET_LAG_MIN_H:
        return ShiftPlan(
            kind="jet_lag", direction="none" if size == 0 else ("advance" if east else "delay"),
            shift_h=size, days_to_adjust=0,
            summary=f"{away} is {rel}. That's usually manageable without a special plan: "
            "get daylight when you land and keep to local bedtimes."
            if size else f"{away} is on the same time as {home}: no jet lag to plan for.",
            tips=tips,
        )

    direction = "advance" if east else "delay"
    sign = -1 if east else 1  # east: bedtimes move earlier
    # Keep completed nights in the count, but only display remaining ones.
    pre_nights = pre_trip_nights(travel)
    pre = pre_trip_shift_h(travel)
    days: list[ShiftDay] = []
    for i, day in enumerate(pre_nights, start=1):
        if day < today:
            continue
        moved = min(pre, PRE_TRIP_H_PER_DAY * i)
        wake = _shift(p.usual_wake, sign * moved)
        bed = _shift(wake, -in_bed)
        light, window = _light(east, bed, wake)
        days.append(ShiftDay(
            day=day, where="home", bedtime=bed, wake=wake, light=light, seek_light=window,
            body_clock_off_h=round(size - moved, 2),
            note=f"Bed and alarm {moved:g} h {'earlier' if east else 'later'} than usual; "
            f"get {light} light.",
        ))

    rate = adjust_rate_h_per_day(shift, p.age_band)
    left = size - pre
    n = math.ceil(left / rate - 1e-9)
    local_bed = _shift(p.usual_wake, -in_bed)
    for j in range(n + 1):
        off = max(0.0, left - rate * j)
        # Target local wake time is not biological wake time while lag remains.
        # Without a phase-aware algorithm, even a small residual lag can put a
        # fixed local window on the wrong side of the circadian nadir.
        light, window = None, None
        note = (
            "Arrival day: stay up until local bedtime; a 10-20 min nap before 3 p.m. if you must."
            if j == 0 else "Body clock back in sync: enjoy your trip." if off == 0
            else f"Body clock ~{fmt_h(off)} {'behind' if east else 'ahead'}; keep to local times."
        )
        if off > 0:
            note += " While your body clock is adjusting, use personalised light timing before seeking bright light at a specific time."
        days.append(ShiftDay(
            day=travel.arrival.date() + timedelta(days=j), where="destination",
            bedtime=local_bed, wake=p.usual_wake, light=light, seek_light=window,
            body_clock_off_h=round(off, 2), note=note,
        ))
    slower = " (east is slower)" if east else ""
    return ShiftPlan(
        kind="jet_lag", direction=direction, shift_h=size, days_to_adjust=n,
        summary=f"{away} is {rel}. Plan a {pre:g} h shift over {len(pre_nights)} preparation nights, "
        f"then allow about {n} days to adjust after landing{slower}. "
        "This estimate assumes the preparation plan is followed; actual adjustment and optimal light timing vary.",
        days=days, tips=tips,
    )


def back_to_term_plan(data: UserData, now: datetime, in_bed_h: float | None = None) -> ShiftPlan | None:
    """Walk a drifted holiday schedule back to term time, 30 min a day."""
    p = data.profile
    in_bed = in_bed_h if in_bed_h is not None else _in_bed_h(data, now)
    recent = primary_nights(data.sessions, now=now)[-7:]
    if p.term_starts is None or not recent:
        return None
    # Advance from the latest completed main sleep, including a followed step.
    # A multi-night median lags actual progress and repeats the previous alarm.
    # Missing new records do not count as proof that a planned step was followed.
    reference = learning.since_noon_h(p.usual_wake)
    current = learning.unwrap_hours(learning.since_noon_h(recent[-1].end), reference)
    late = round(current - reference, 2)
    if late <= 0.25:
        return ShiftPlan(
            kind="back_to_term", direction="none", shift_h=max(late, 0.0), days_to_adjust=0,
            summary="Your sleep is already on term time: nothing to shift.",
        )
    needed = math.ceil(late / BACK_TO_TERM_H_PER_DAY - 1e-9)
    tonight = (now - timedelta(hours=5)).date()  # before 05:00 it's still last night
    start = max(tonight, p.term_starts - timedelta(days=needed))
    days = []
    d, step = start, 0
    while d < p.term_starts:  # the last night ends on the first morning of term
        step += 1
        remaining = max(0.0, late - BACK_TO_TERM_H_PER_DAY * step)
        wake = _shift(p.usual_wake, remaining)
        days.append(ShiftDay(
            day=d, where="home", bedtime=_shift(wake, -in_bed), wake=wake,
            light="morning", seek_light=(wake, _shift(wake, MORNING_LIGHT_H)),
            body_clock_off_h=round(remaining, 2),
            note="Alarm 30 min earlier than yesterday; daylight soon after waking." if remaining
            else "Back on term time.",
        ))
        d += timedelta(days=1)
    short = days[-1].body_clock_off_h if days else late
    summary = (
        f"You're waking about {fmt_h(late)} later than term time. Doze moves your wake time back "
        f"by up to 30 min a day from {start:%a %d %b}."
    )
    if short > 0:
        summary += f" Start today: you'll still be ~{fmt_h(short)} late on the first day."
    else:
        summary += f" You're on {p.usual_wake:%H:%M} by {p.term_starts:%a %d %b}."
    return ShiftPlan(
        kind="back_to_term", direction="advance", shift_h=late, days_to_adjust=needed,
        summary=summary, days=days, tips=list(MODES[Mode.HOLIDAY].tips),
    )
