"""Nap recommendations: press the button, get the alarm.

Doze only offers the naps the research backs (Brief §3):
* 10 min: immediate lift, lasting up to ~155 min (Brooks & Lack 2006);
* 20 min: the lift appears ~35 min after waking and lasts up to ~125 min;
* 90 min: one full cycle, so you wake from light sleep. Only if you say you're
  very tired and it's early afternoon;
* never 30-60 min: that's when you're most likely to wake from deep sleep.

Between the allowed options, the sleep model simulates each one and picks the
nap that gives the most *alert minutes* from waking until tonight's bedtime:

    score = sum(alertness(t) * weight(t) * dt, over awake time after the alarm)
            - 6000 * (sleep pressure the nap takes away from tonight)

Time asleep scores zero, so a nap has to earn its keep, and calendar events
count extra (an exam x2-3). The alarm leaves ~20 min for grogginess to fade
before an exam or a drive, naps after 3 p.m. get a warning (none at all for
65+, NIA), and every recommendation shows its cost to tonight. Saying you
feel fine can't cancel sleep debt: people under-count it (Van Dongen et al.
2003, Brief §2), so self-reports can only raise the model's estimate.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, time, timedelta

from app.schemas import (
    AgeBand,
    AlertnessPoint,
    CalendarEvent,
    EventCategory,
    NapRecommendation,
    Tiredness,
    UserData,
)
from app.services.modes import GROGGY_MIN, MODES, effective_mode
from app.services.planner import recommend_bedtime
from app.services.sleep_model import (
    AFTERNOON_DIP_H,
    PersonModel,
    SleepBlock,
    _clamp,
    alertness,
    clock_h,
    fmt_h,
    hours_between,
    simulate,
    time_grid,
)

POWER_NAPS = (10, 20)
FULL_CYCLE = 90
EARLY_AFTERNOON = (time(12, 0), time(15, 0))  # our choice for "early afternoon"
LATE_NAP = time(15, 0)  # after 3 p.m. can make night sleep harder (Mayo Clinic)
TOO_CLOSE_TO_BED_H = 2.0  # our choice: closer than this, go to bed early instead
DEFAULT_BUFFER_MIN = 10  # our choice; exams and drives get GROGGY_MIN (20)
SOON_AFTER_ALARM_MIN = 60

SELF_REPORT = {Tiredness.FINE: 0.30, Tiredness.TIRED: 0.60, Tiredness.VERY_TIRED: 0.80}
MODEL_SD, REPORT_SD = 0.12, 0.15
NIGHT_COST = 6000.0  # alert-minutes per unit of sleep pressure lost by bedtime (our choice)
STEP_MIN = 5
NAP_EFFICIENCY = 0.95

LENGTH_WHY = {
    10: "10 minutes gives an immediate lift that can last up to ~2.5 hours (Brooks & Lack 2006).",
    20: "20 minutes: the lift kicks in ~35 minutes after waking and lasts up to ~2 hours (Brooks & Lack 2006).",
    90: "You're very tired and it's early afternoon, so a full ~90-minute cycle lets you wake from "
    "light sleep instead of deep sleep.",
}
NO_MID_NAPS = "Doze never plans 30-60 minute naps: that's when you're most likely to wake from deep sleep, groggy."


def fuse_tiredness(model_pressure: float, tiredness: Tiredness | None) -> float:
    """Combine the model's sleep-pressure estimate with what you told us.

    A precision-weighted average (one Bayesian / Kalman step: the watch-based
    model is the prior, your report a noisy measurement), but only allowed to
    push the estimate up.
    """
    if tiredness is None:
        return model_pressure
    w = (1 / REPORT_SD**2) / (1 / MODEL_SD**2 + 1 / REPORT_SD**2)
    return max(model_pressure, (1 - w) * model_pressure + w * SELF_REPORT[tiredness])


def time_to_fall_asleep_min(pressure: float, usual_min: float) -> float:
    """Sleepier people drop off faster."""
    return _clamp(usual_min * (1.6 - 1.2 * pressure), 3, 30)


def _event_at(events: list[CalendarEvent], t: datetime) -> CalendarEvent | None:
    return next((e for e in events if e.start <= t < e.end and e.category != EventCategory.REST), None)


def _next_event(events: list[CalendarEvent], t: datetime) -> CalendarEvent | None:
    upcoming = [e for e in events if e.start >= t and e.category != EventCategory.REST]
    return min(upcoming, key=lambda e: e.start) if upcoming else None


def _buffer_min(event: CalendarEvent) -> int:
    if event.category in (EventCategory.EXAM, EventCategory.TRAVEL):
        return GROGGY_MIN
    return DEFAULT_BUFFER_MIN


def _weight(events: list[CalendarEvent], t: datetime, weights: dict[EventCategory, float]) -> float:
    e = _event_at(events, t)
    return weights[e.category] if e else 1.0


def _score(states, phys, weights: list[float], start: datetime) -> float:
    total = 0.0
    for s, w in zip(states, weights):
        if s.at >= start:
            a = alertness(s, phys)
            if a is not None:
                total += a * w * STEP_MIN
    return total


def _band(pct: float) -> str:
    return "tiny" if pct < 5 else "small" if pct < 12 else "noticeable" if pct < 20 else "big"


def _points(states, phys) -> list[AlertnessPoint]:
    return [AlertnessPoint(at=s.at, alertness=alertness(s, phys)) for s in states]


def _plan(
    model: PersonModel, lie_down: datetime, tiredness: Tiredness | None, bedtime: datetime
) -> NapRecommendation:
    data, p = model.data, model.data.profile
    mode, _ = effective_mode(data, lie_down)
    state = model.state if lie_down == model.now else model.state_at(lie_down)
    fatigue = model.phys.fatigue
    start = replace(state, pressure=_clamp(fuse_tiredness(state.pressure + fatigue, tiredness) - fatigue))
    alert_now = alertness(start, model.phys)

    def no_nap(headline: str, reasons: list[str], warnings: list[str] | None = None) -> NapRecommendation:
        return NapRecommendation(
            should_nap=False, kind="none", lie_down=lie_down, alarm=None, sleep_min=0,
            alertness_now=alert_now, alertness_after=None, tonight_cost_pct=0.0,
            headline=headline, reasons=reasons, warnings=warnings or [],
        )

    current = _event_at(data.events, lie_down)
    if current is not None:
        return no_nap(
            f"You're in {current.title} until {current.end:%H:%M}",
            ["Press the nap button again afterwards and Doze will plan one."],
        )
    if p.age_band == AgeBand.OLDER and lie_down.time() >= LATE_NAP:
        return no_nap(
            "No nap this late in the day",
            [
                "The NIA advises against naps late in the afternoon or evening for over-65s: "
                "they can make night sleep harder.",
                f"Tonight's planned bedtime is {bedtime:%H:%M}.",
            ],
        )
    if hours_between(lie_down, bedtime) < TOO_CLOSE_TO_BED_H:
        return no_nap(
            "Too close to bedtime: go to bed early instead",
            [
                f"Bedtime is {bedtime:%H:%M}, so a nap now would eat into tonight's sleep.",
                "If you're struggling to stay awake, start winding down now and go to bed early.",
            ],
        )

    nxt = _next_event(data.events, lie_down)
    deadline = bedtime - timedelta(hours=TOO_CLOSE_TO_BED_H)
    if nxt is not None:
        deadline = min(deadline, nxt.start - timedelta(minutes=nxt.travel_min + _buffer_min(nxt)))
    latency = time_to_fall_asleep_min(start.pressure, model.personal.latency_min)
    onset = lie_down + timedelta(minutes=latency)
    early_afternoon = EARLY_AFTERNOON[0] <= lie_down.time() < EARLY_AFTERNOON[1]
    options = [*POWER_NAPS, *([FULL_CYCLE] if tiredness == Tiredness.VERY_TIRED and early_afternoon else [])]

    times = time_grid(lie_down, bedtime, STEP_MIN)
    event_weights = MODES[mode].event_weights
    weights = [_weight(data.events, t, event_weights) for t in times]
    without = simulate(start, [], times)
    base_score = _score(without, model.phys, weights, lie_down)
    base_bed = without[-1].pressure

    results, too_long = [], []
    for minutes in options:
        alarm = onset + timedelta(minutes=minutes)
        if alarm > deadline:
            too_long.append(minutes)
            continue
        states = simulate(start, [SleepBlock(onset, alarm, NAP_EFFICIENCY, is_nap=True)], times)
        cost = max(0.0, base_bed - states[-1].pressure)
        score = _score(states, model.phys, weights, alarm) - NIGHT_COST * cost
        results.append((score, minutes, alarm, states, cost))

    if not results:
        what = f"{nxt.title} at {nxt.start:%H:%M}" if nxt and deadline < bedtime - timedelta(hours=TOO_CLOSE_TO_BED_H) else "bedtime"
        return no_nap(
            "Not enough time for a nap",
            [
                f"Even a 10-minute nap (plus ~{latency:.0f} min to fall asleep) wouldn't finish in time "
                f"before {what}.",
                "Try five minutes with your eyes closed, or a short walk in daylight.",
            ],
        )

    score, minutes, alarm, states, cost = max(results, key=lambda r: r[0])
    should = score > base_score
    kind = "full_cycle" if minutes == FULL_CYCLE else "power"
    after_t = alarm + timedelta(minutes=30)
    after = next((alertness(s, model.phys) for s in states if s.at >= after_t), None)
    cost_pct = round(100 * cost / base_bed, 1) if base_bed else 0.0

    reasons = [f"Alarm {alarm:%H:%M}: about {latency:.0f} min to fall asleep, then {minutes} min of sleep.", LENGTH_WHY[minutes]]
    if too_long and nxt is not None and minutes < max(too_long):
        reasons.append(f"A longer nap wouldn't leave time to wake up properly before {nxt.title}.")
    reasons.append(NO_MID_NAPS)
    last = model.last_night
    if last is not None and last.hours_asleep < model.need_h - 0.5:
        reasons.append(
            f"You slept {fmt_h(last.hours_asleep)} last night, "
            f"{fmt_h(model.need_h - last.hours_asleep)} under your {fmt_h(model.need_h)} need."
        )
    if model.debt_h >= 1:
        reasons.append(f"You're carrying {fmt_h(model.debt_h)} of sleep debt.")
    if model.hrv and model.hrv[1] <= -10:
        reasons.append(f"Your HRV is {-model.hrv[1]:.0f}% below your 7-day baseline, a sign your body is still recovering.")
    if tiredness in (Tiredness.TIRED, Tiredness.VERY_TIRED):
        reasons.append(f"You said you're {tiredness.value.replace('_', ' ')}.")
    if tiredness == Tiredness.FINE and model.debt_h >= 3:
        reasons.append(
            f"You feel fine, but you're carrying {fmt_h(model.debt_h)} of sleep debt. After a week of "
            "short nights people stop noticing how tired they are (Van Dongen et al. 2003)."
        )
    dip = (model.phys.mid_sleep_h + AFTERNOON_DIP_H - 0.5) % 24
    if abs(clock_h(lie_down) - dip) <= 1.25:
        reasons.append("You're in your body clock's afternoon dip, a natural time to nap.")
    reasons.append(
        f"Cost to tonight: uses about {cost_pct:.0f}% of tonight's sleep pressure ({_band(cost_pct)}); "
        f"bedtime stays {bedtime:%H:%M}."
    )
    if not should:
        reasons.append("Your predicted alertness holds up without one: a walk in daylight may do more.")

    warnings = []
    if lie_down.time() >= LATE_NAP:
        warnings.append("It's after 3 p.m.: late naps can make it harder to sleep well tonight (Mayo Clinic).")
    if (
        nxt is not None
        and nxt.category in (EventCategory.EXAM, EventCategory.TRAVEL)
        and nxt.start - alarm <= timedelta(minutes=SOON_AFTER_ALARM_MIN)
    ):
        warnings.append(
            f"Give yourself 20 minutes after the alarm before {nxt.title}: grogginess usually fades "
            "within about 20 minutes, and bright light helps (Sleep Research Society)."
        )

    label = "full-cycle nap" if kind == "full_cycle" else "power nap"
    headline = (
        f"Nap now: alarm at {alarm:%H:%M} ({minutes}-minute {label})"
        if should
        else f"You don't really need a nap. If you want one: alarm at {alarm:%H:%M} ({minutes} min)"
    )
    return NapRecommendation(
        should_nap=should, kind=kind, lie_down=lie_down, alarm=alarm, sleep_min=minutes,
        alertness_now=alert_now, alertness_after=after, tonight_cost_pct=cost_pct,
        headline=headline, reasons=reasons, warnings=warnings,
        with_nap=_points(states, model.phys), without_nap=_points(without, model.phys),
    )


def recommend_nap(
    data: UserData, now: datetime, tiredness: Tiredness | None = None, model: PersonModel | None = None
) -> NapRecommendation:
    """The nap button: lie down now, here's your alarm. ``tiredness`` may be the
    enum or its string value straight from a form ("tired", "very_tired")."""
    tiredness = Tiredness(tiredness) if tiredness is not None else None
    model = model or PersonModel(data, now)
    bedtime = recommend_bedtime(data, now, model).bedtime
    return _plan(model, now, tiredness, bedtime)


SLOT_WINDOW = (time(12, 0), time(15, 0))  # early afternoon only (Brief §3)


def best_nap_slot(data: UserData, now: datetime, model: PersonModel | None = None) -> NapRecommendation | None:
    """For the calendar: the free early-afternoon moment today when a nap helps most
    (lowest predicted alertness), or None if no nap is needed or nothing fits."""
    model = model or PersonModel(data, now)
    bedtime = recommend_bedtime(data, now, model).bedtime
    day = now.date()
    start = max(now, datetime.combine(day, SLOT_WINDOW[0]))
    end = datetime.combine(day, SLOT_WINDOW[1])
    best: tuple[float, NapRecommendation] | None = None
    for t in time_grid(start.replace(second=0, microsecond=0), end, 15):
        if _event_at(data.events, t) is not None:
            continue
        plan = _plan(model, t, None, bedtime)
        if not plan.should_nap:
            continue
        if best is None or (plan.alertness_now or 100) < best[0]:
            best = (plan.alertness_now or 100, plan)
    if best is None:
        return None
    plan = best[1]
    return plan.model_copy(update={
        "headline": f"Best nap today: lie down at {plan.lie_down:%H:%M}, alarm {plan.alarm:%H:%M} "
        f"({plan.sleep_min}-minute power nap)",
    })
