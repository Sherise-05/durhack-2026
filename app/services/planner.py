"""Plans built on the sleep model: the dashboard snapshot, tonight's bedtime and
alarm, and the what-if simulator.

Bedtime rules (Sleep Science Brief):
* never plan less than your band's minimum (7 h adults, 8 h teens), and say so
  plainly when the calendar makes that impossible (§1, §11);
* add back part of your running sleep debt, up to +1 h (more than 9 h can be
  right when recovering from debt, AASM; §1, §2);
* alarm leaves time to get ready and travel, and 20 min for grogginess to fade
  before an exam or a drive (§3, §5);
* exam mode: same wake time every day, bed before 2 a.m. (§5);
* wind-down includes screens down or dimmed (§4);
* flag hard exercise within an hour of bed and evening drinks (§8).
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from app.schemas import (
    AlertnessPoint,
    BedtimeRecommendation,
    CalendarEvent,
    EventCategory,
    EventForecast,
    Mode,
    Snapshot,
    UserData,
    WhatIf,
)
from app.services import learning
from app.services.modes import (
    EXAM_LATEST_BEDTIME,
    MODES,
    back_to_term_plan,
    effective_mode,
    exam_wake_time,
    jet_lag_plan,
    lead_min,
    life_stage_tips,
)
from app.services.sleep_model import (
    CYCLE_MIN,
    PersonModel,
    SleepBlock,
    day_strain,
    fmt_h,
    gp_card,
    hours_between,
    night_stats,
    readiness,
    social_jet_lag_h,
)

NIGHT_STARTS_H = 5  # before 05:00 you're still on last night
BODY_CLOCK_SLACK_H = 1.5  # our choice: earlier than this before your natural bedtime -> heads-up
LATE_EXERCISE_MIN = 60  # Brief §8
HOME_SETTLE_MIN = 15  # our choice: get home from a late event and settle before lights out
LONG_DAY_H = 17  # attention lapses climb past ~16 h awake (Van Dongen et al. 2003)

# Latest time to finish a drink before bed (Gardiner et al. 2023 meta-analysis, Brief §8).
# No cut-off was found for black tea. Only show these if the user logs caffeine.
CAFFEINE_CUTOFF_H = {"coffee": 8.8, "pre-workout": 13.2}


def night_of(now: datetime) -> date:
    return (now - timedelta(hours=NIGHT_STARTS_H)).date()


def _floor5(dt: datetime) -> datetime:
    return dt.replace(second=0, microsecond=0) - timedelta(minutes=dt.minute % 5)


def _ceil5(dt: datetime) -> datetime:
    floored = _floor5(dt)
    return floored if floored == dt else floored + timedelta(minutes=5)


def first_commitment(events: list[CalendarEvent], morning: date) -> CalendarEvent | None:
    todays = [
        e for e in events
        if e.start.date() == morning and e.start.time() >= time(4) and e.category != EventCategory.REST
    ]
    return min(todays, key=lambda e: e.start) if todays else None


def caffeine_cutoffs(bedtime: datetime) -> dict[str, datetime]:
    return {drink: bedtime - timedelta(hours=h) for drink, h in CAFFEINE_CUTOFF_H.items()}


# --------------------------------------------------------------------------- dashboard


def snapshot(data: UserData, now: datetime, model: PersonModel | None = None) -> Snapshot:
    model = model or PersonModel(data, now)
    mode, reason = effective_mode(data, now)
    last = model.last_night
    hrv_z, hrv_pct = model.hrv if model.hrv else (None, None)
    return Snapshot(
        now=now,
        mode=mode,
        mode_reason=reason,
        readiness=readiness(last, model.need_h, model.debt_h, hrv_z),
        alertness_now=model.alertness_now,
        sleep_need_h=round(model.need_h, 2),
        sleep_debt_h=model.debt_h,
        last_night=night_stats(last, model.need_h) if last else None,
        hrv_vs_baseline_pct=hrv_pct,
        social_jet_lag_h=social_jet_lag_h(data, now),
        day_strain=day_strain(data.events, now.date()),
        tips=list(MODES[mode].tips) + life_stage_tips(data.profile),
        gp_card=gp_card(data, now),
    )


def day_curve(data: UserData, now: datetime, model: PersonModel | None = None) -> list[AlertnessPoint]:
    """Today's alertness, from this morning's wake-up to an hour after tonight's
    planned bedtime (the gap at the end is the planned sleep)."""
    model = model or PersonModel(data, now)
    night = recommend_bedtime(data, now, model)
    woke = model.state.woke_at or now - timedelta(hours=4)
    tonight = SleepBlock(night.bedtime, night.alarm, model.efficiency)
    return model.curve(woke, night.bedtime + timedelta(hours=1), extra=[tonight])


# --------------------------------------------------------------------------- tonight


def recommend_bedtime(
    data: UserData, now: datetime, model: PersonModel | None = None
) -> BedtimeRecommendation:
    model = model or PersonModel(data, now)
    p = data.profile
    mode, _ = effective_mode(data, now)
    settings = MODES[mode]
    night = night_of(now)
    morning = night + timedelta(days=1)
    reasons: list[str] = []
    warnings: list[str] = []
    minimum, _ = learning.need_range_h(p)
    need = model.need_h
    extra = min(model.debt_h * settings.debt_repay_fraction, settings.max_extra_sleep_h)
    target = max(minimum, need + extra)
    in_bed_h = target / model.efficiency

    # 1. When you need to be up
    plan = (
        jet_lag_plan(data, night, in_bed_h) if mode == Mode.TRAVEL
        else back_to_term_plan(data, now, in_bed_h) if mode == Mode.HOLIDAY
        else None
    )
    plan_night = next((d for d in plan.days if d.day == night), None) if plan else None
    if plan_night is not None:
        usual_alarm = datetime.combine(morning, plan_night.wake)
        label = "jet-lag plan" if plan.kind == "jet_lag" else "back-to-term plan"
        reasons.append(f"Tonight follows your {label}: alarm {plan_night.wake:%H:%M}. {plan_night.note}")
    elif mode == Mode.EXAM:
        usual_alarm = datetime.combine(morning, exam_wake_time(data, now))
    else:
        usual_alarm = datetime.combine(morning, p.usual_wake) + timedelta(hours=settings.lie_in_h)
    event = first_commitment(data.events, morning)
    alarm = usual_alarm
    if event is not None:
        lead = lead_min(p, event)
        needed = event.start - timedelta(minutes=lead)
        if needed < alarm:
            alarm = needed
            why = (
                "get ready, travel and let the ~20 min of grogginess wear off"
                if event.category in (EventCategory.EXAM, EventCategory.TRAVEL)
                else "get ready and get there"
            )
            reasons.append(
                f"First up tomorrow: {event.title} at {event.start:%H:%M}. The alarm leaves {lead} min to {why}."
            )
        else:
            reasons.append(f"First up tomorrow: {event.title} at {event.start:%H:%M}, with time to spare.")
    if mode == Mode.EXAM and alarm == usual_alarm and plan_night is None:
        reasons.append(
            f"Exam mode keeps the same {alarm:%H:%M} alarm every day, so your body clock is "
            "already there on exam morning (regular sleepers got better grades: Phillips et al. 2017)."
        )
    elif event is None and plan_night is None:
        reasons.append(
            f"Nothing early tomorrow: alarm at {alarm:%H:%M}, close to your usual "
            f"{p.usual_wake:%H:%M}. A steady wake time matters more than lie-ins."
        )
    alarm = _floor5(alarm)

    # 2. How much sleep tonight
    bedtime = _floor5(alarm - timedelta(hours=in_bed_h))
    if model.personal.need.n >= 3:
        reasons.append(
            f"Doze has learned you need about {fmt_h(need)} "
            f"(from {model.personal.need.n} morning check-ins)."
        )
    else:
        reasons.append(
            f"Planning for {fmt_h(need)} of sleep, the default for your age group. "
            "Morning check-ins will fine-tune it."
        )
    if extra >= 5 / 60:
        reasons.append(
            f"You're carrying {fmt_h(model.debt_h)} of sleep debt, so tonight adds {round(extra * 60)} min."
        )

    # 3. Body clock: there's no point planning lights-out hours before you can fall
    # asleep, so debt repayment gives way to it, but never below the minimum (§1, §4).
    mid = model.phys.mid_sleep_h
    natural = datetime.combine(night, time(12)) + timedelta(hours=(mid - 12) % 24 - need / 2)
    body_clock_earliest = _floor5(natural - timedelta(hours=BODY_CLOCK_SLACK_H))
    latest_for_minimum = _floor5(alarm - timedelta(hours=minimum / model.efficiency))
    if bedtime < body_clock_earliest:
        bedtime = min(body_clock_earliest, latest_for_minimum)
        reasons.append(
            f"Your body clock is unlikely to let you sleep much before {body_clock_earliest:%H:%M}, "
            "so tonight's catch-up is trimmed to fit."
        )
    if bedtime < body_clock_earliest:
        warnings.append(
            f"Your body clock may not feel sleepy until about {body_clock_earliest:%H:%M}. "
            "Get daylight soon after waking to shift it earlier."
        )

    # 4. Rules and reality checks
    if settings.latest_bedtime is not None:
        bedtime = min(bedtime, datetime.combine(morning, settings.latest_bedtime))
    earliest = _ceil5(now + timedelta(minutes=10))
    if bedtime < earliest:
        bedtime = earliest
    clashes = [e for e in data.events if e.category != EventCategory.REST and e.start < bedtime < e.end]
    if clashes:
        last = max(clashes, key=lambda e: e.end)
        bedtime = _ceil5(last.end + timedelta(minutes=last.travel_min + HOME_SETTLE_MIN))
        reasons.append(f"{last.title} runs until {last.end:%H:%M}, so lights out moves to {bedtime:%H:%M}.")
    sleep_h = max(0.0, hours_between(bedtime, alarm) * model.efficiency)
    if sleep_h < minimum - 0.05:
        warnings.append(
            f"You can only get about {fmt_h(sleep_h)} of sleep before your {alarm:%H:%M} alarm, "
            f"under the {minimum:g} h minimum. Doze won't pretend that's enough: go to bed as soon as you can."
        )
    if mode == Mode.EXAM and bedtime > datetime.combine(morning, EXAM_LATEST_BEDTIME):
        warnings.append(
            "It's past 2 a.m., the latest bedtime exam mode plans for: later bedtimes were linked "
            "to worse test scores even with the same total sleep (Okano et al. 2019)."
        )
    evening = datetime.combine(night, time(18))
    for e in data.events:
        if e.category == EventCategory.EXERCISE and e.start < bedtime and e.end > bedtime - timedelta(minutes=LATE_EXERCISE_MIN):
            warnings.append(
                f"{e.title} ends at {e.end:%H:%M}, within an hour of bedtime. Evening exercise is fine, "
                "but a very hard session this close to bed may delay falling asleep."
            )
        if e.category == EventCategory.SOCIAL and e.start.date() == night and e.end >= evening:
            reasons.append(
                f"If there are drinks at {e.title}: alcohol helps you drop off but makes the second "
                "half of the night lighter."
            )
    woke = model.state.woke_at
    if woke is not None and hours_between(woke, bedtime) > LONG_DAY_H:
        reasons.append(
            f"By bedtime you'll have been awake {hours_between(woke, bedtime):.0f} h. Attention lapses "
            "climb steadily after ~16 h awake (Van Dongen et al. 2003)."
        )

    wind_down = bedtime - timedelta(minutes=settings.wind_down_min)
    reasons.append(f"Wind down from {wind_down:%H:%M}: screens down or dimmed, lights low.")
    cycles = sleep_h * 60 / CYCLE_MIN
    reasons.append(f"That's about {fmt_h(sleep_h)} asleep, roughly {cycles:.0f} sleep cycles.")
    return BedtimeRecommendation(
        wind_down=wind_down, bedtime=bedtime, alarm=alarm, sleep_h=round(sleep_h, 2),
        need_h=round(need, 2), debt_h=model.debt_h, cycles=round(cycles, 1),
        first_event=event.title if event else None,
        headline=f"Lights out {bedtime:%H:%M}, alarm {alarm:%H:%M}",
        reasons=reasons, warnings=warnings,
    )


# --------------------------------------------------------------------------- what-if


def what_if_bedtime(
    data: UserData, now: datetime, bedtime: datetime, alarm: datetime, model: PersonModel | None = None
) -> WhatIf:
    """'If I sleep from bedtime to alarm, how sharp will I be tomorrow?'"""
    model = model or PersonModel(data, now)
    mode, _ = effective_mode(data, now)
    minimum, _ = learning.need_range_h(data.profile)
    asleep = bedtime < alarm
    blocks = [SleepBlock(bedtime, alarm, model.efficiency)] if asleep else []
    sleep_h = hours_between(bedtime, alarm) * model.efficiency if asleep else 0.0
    wake = max(alarm, bedtime)
    curve = model.curve(wake, wake + timedelta(hours=16), extra=blocks)
    forecasts = []
    for e in sorted(data.events, key=lambda e: e.start):
        if e.start.date() != wake.date() or e.start < wake:
            continue
        during = [pt.alertness for pt in curve if e.start <= pt.at <= e.end and pt.alertness is not None]
        if during:
            forecasts.append(EventForecast(title=e.title, start=e.start, alertness=round(sum(during) / len(during), 1)))

    warnings = []
    if sleep_h < 1:
        warnings.append(
            "That's an all-nighter, and Doze won't plan one: after 35 h awake students remembered new "
            "material 19-40 % worse (Yoo et al. 2007), and regular all-nighters are linked to lower GPAs."
        )
    elif sleep_h < minimum - 0.05:
        warnings.append(f"That's about {fmt_h(sleep_h)} of sleep, under the {minimum:g} h minimum.")
    if mode == Mode.EXAM and asleep and bedtime > datetime.combine(alarm.date(), EXAM_LATEST_BEDTIME):
        warnings.append(
            "Bedtimes after ~2 a.m. were linked to worse test scores even with the same total sleep "
            "(Okano et al. 2019)."
        )
    return WhatIf(bedtime=bedtime, alarm=alarm, sleep_h=round(sleep_h, 2), curve=curve, events=forecasts, warnings=warnings)
