"""Doze's sleep model: the maths behind every number on the dashboard.

The core is the **two-process model** of sleep regulation (Borbély 1982), the
standard model in sleep science, plus sleep inertia from the three-process
model (Åkerstedt & Folkard):

* **Process S, sleep pressure** (0-1). Builds while you're awake, drains while
  you sleep:
      awake:  S(t) = 1 - (1 - S0) * exp(-t / 18.2 h)
      asleep: S(t) = S0 * exp(-t * efficiency / 4.2 h)
  Time constants from Daan, Beersma & Borbély (1984). Only sleep time and
  efficiency drive it; sleep stages are display-only (Brief §9).
* **Process C, the body clock.** A 24 h wave plus a 12 h harmonic: that's the
  afternoon dip (~1-3 pm for most people) and the evening second wind. It runs
  on clock time, phased by your body-clock belief (learning.py). After a flight
  it moves ~1 h/day towards local time, slower going east (Brief §4, §6).
* **Process W, sleep inertia.** Grogginess after waking that fades over ~20 min
  (Brief §3), much stronger if you wake from deep sleep. That's why naps are
  10 or 20 min, or a whole ~90 min cycle, and never 30-60.
* **Nap boost.** The short-term lift from a nap, fading over ~2.5 h (Brooks &
  Lack 2006 saw benefits lasting 125-155 min).

    alertness = 97 + 25*C - 85*(S + fatigue) - W + boost - debt_penalty  (0-100)

``fatigue`` nudges S up when last night's HRV is below your own 7-day baseline
(Brief §9). ``debt_penalty`` is 1 point per hour of running sleep debt (max 8):
after two weeks of short nights performance keeps falling even though people
stop feeling sleepier (Van Dongen et al. 2003, Brief §2).

The weights are our choice, tuned so a well-rested sleeper peaks ~82 around
9 a.m., dips ~10 points in the early afternoon (1-3 pm), gets a second wind
around 6 p.m. and is down near 40 by bedtime.
Everything here is a pure function of the data: no I/O and no clock reads,
always pass ``now``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from statistics import median, pstdev
from zoneinfo import ZoneInfo

from app.schemas import (
    AgeBand,
    AlertnessPoint,
    CalendarEvent,
    Card,
    EventCategory,
    Mode,
    NightStats,
    SleepSession,
    TravelPlan,
    UserData,
)
from app.services import learning

# Process S
TAU_RISE_H = 18.2
TAU_DECAY_H = 4.2
S_START = 0.6  # pressure at the first bedtime we know about (washes out within ~2 nights)

# Process C (hours after mid-sleep)
CIRCADIAN_PEAK_H = 13.5
AFTERNOON_DIP_H = 11.0
HARMONIC = 0.5

# Alertness weights (our choice)
BASE = 97.0
K_CIRCADIAN = 25.0
K_PRESSURE = 85.0

# Process W: sleep inertia
INERTIA_POINTS = 30.0
INERTIA_TAU_H = 1 / 3  # ~20 min

# Nap boost
NAP_BOOST_POINTS = 8.0
NAP_BOOST_TAU_H = 2.5

# Debt and HRV
DEBT_NIGHTS = 14
DEBT_POINTS_PER_H = 1.0
MAX_DEBT_POINTS = 8.0
HRV_BASELINE_NIGHTS = 7
HRV_FATIGUE_PER_SD = 0.04

# Jet lag (Brief §6)
JET_LAG_MIN_H = 3.0  # under this: tips only
PRE_TRIP_DAYS = 3
PRE_TRIP_H_PER_DAY = 1.0
WEST_H_PER_DAY = 1.0  # ~1 day per time zone
EAST_H_PER_DAY = 0.75  # slower going east (our choice of how much slower)
OLDER_JET_LAG_FACTOR = 0.8  # 65+ may take longer (WebMD; our choice of how much)

CYCLE_MIN = 90
TYPICAL_EFFICIENCY = 0.9


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def hours_between(a: datetime, b: datetime) -> float:
    return (b - a).total_seconds() / 3600


def fmt_h(hours: float) -> str:
    """2.67 -> '2h 40m', 0.17 -> '10m'."""
    total = max(0, round(hours * 60))
    h, m = divmod(total, 60)
    return f"{h}h {m:02d}m" if h else f"{m}m"


def clock_h(t: datetime) -> float:
    return t.hour + t.minute / 60 + t.second / 3600


def nights(sessions: list[SleepSession], now: datetime) -> list[SleepSession]:
    """Night sleeps (not naps) that had ended by ``now``, oldest first."""
    return sorted((s for s in sessions if not s.is_nap and s.end <= now), key=lambda s: s.end)


# --------------------------------------------------------------------------- the processes


def pressure_awake(s0: float, hours: float) -> float:
    return 1 - (1 - s0) * math.exp(-hours / TAU_RISE_H)


def pressure_asleep(s0: float, hours: float, efficiency: float = 1.0) -> float:
    return s0 * math.exp(-hours * efficiency / TAU_DECAY_H)


def circadian(clock: float, mid_sleep: float) -> float:
    """Body-clock alertness drive, about -1.5 (night) to +1.5 (evening peak)."""
    x = clock - mid_sleep
    return math.cos(2 * math.pi * (x - CIRCADIAN_PEAK_H) / 24) - HARMONIC * math.cos(
        4 * math.pi * (x - AFTERNOON_DIP_H) / 24
    )


def deep_sleep_chance(minutes_asleep: float, pressure: float) -> float:
    """How likely you are to be in deep sleep this many minutes after falling asleep.

    Deep sleep peaks ~40 min in (sooner when you're short of sleep) and again,
    less strongly, one ~90 min cycle later.
    """
    centre = 40 - 20 * (pressure - 0.5)
    first = math.exp(-0.5 * ((minutes_asleep - centre) / 12) ** 2)
    second = 0.5 * math.exp(-0.5 * ((minutes_asleep - centre - CYCLE_MIN) / 15) ** 2)
    return _clamp(0.1 + 0.9 * max(first, second))


def nap_boost(minutes_asleep: float) -> float:
    return NAP_BOOST_POINTS * (1 - math.exp(-minutes_asleep / 8))


# --------------------------------------------------------------------------- simulation


@dataclass(frozen=True)
class SleepBlock:
    """A stretch of sleep for the model to play out, real or hypothetical."""

    start: datetime  # asleep from
    end: datetime  # awake at
    efficiency: float = TYPICAL_EFFICIENCY
    is_nap: bool = False

    @property
    def minutes_asleep(self) -> float:
        return hours_between(self.start, self.end) * 60 * self.efficiency


@dataclass(frozen=True)
class BodyState:
    at: datetime
    pressure: float
    asleep: bool = False
    woke_at: datetime | None = None
    wake_depth: float = 0.0  # 0-1, how deep you were when you last woke
    boost: float = 0.0  # nap boost (points) at boost_at
    boost_at: datetime | None = None


@dataclass(frozen=True)
class Physiology:
    """One person's constants for the day."""

    mid_sleep_h: float  # body-clock phase
    fatigue: float = 0.0  # extra pressure from low HRV
    debt_h: float = 0.0
    inertia_scale: float = 1.0  # learned grogginess sensitivity


def blocks_from_sessions(sessions: list[SleepSession]) -> list[SleepBlock]:
    out: list[SleepBlock] = []
    for s in sorted(sessions, key=lambda s: s.start):
        start = s.start if not out else max(s.start, out[-1].end)  # ignore overlaps
        if start < s.end:
            out.append(SleepBlock(start, s.end, s.efficiency, s.is_nap))
    return out


def _wake_depth(block: SleepBlock, onset_pressure: float, end_pressure: float) -> float:
    if block.is_nap or block.minutes_asleep < 150:
        return deep_sleep_chance(block.minutes_asleep, onset_pressure)
    return _clamp(0.3 + 0.6 * end_pressure, 0.3, 0.8)  # mornings: groggier after short nights


def advance(state: BodyState, blocks: list[SleepBlock], until: datetime) -> BodyState:
    """Play the model forward from ``state`` to ``until`` through sorted sleep blocks."""
    s, t = state.pressure, state.at
    woke_at, depth, boost, boost_at = state.woke_at, state.wake_depth, state.boost, state.boost_at
    asleep = False
    for b in blocks:
        if b.end <= t:
            continue
        if b.start >= until:
            break
        start = max(b.start, t)
        s = pressure_awake(s, hours_between(t, start))
        stop = min(b.end, until)
        s = pressure_asleep(s, hours_between(start, stop), b.efficiency)
        t = stop
        if b.end > until:
            asleep = True
            break
        onset = s / math.exp(-hours_between(b.start, b.end) * b.efficiency / TAU_DECAY_H)
        woke_at, depth = b.end, _wake_depth(b, onset, s)
        if b.is_nap:
            boost, boost_at = nap_boost(b.minutes_asleep), b.end
    if not asleep:
        s = pressure_awake(s, hours_between(t, until))
    return BodyState(until, s, asleep, woke_at, depth, boost, boost_at)


def alertness(state: BodyState, phys: Physiology) -> float | None:
    """Predicted alertness 0-100, or None while asleep."""
    if state.asleep:
        return None
    inertia = boost = 0.0
    if state.woke_at is not None:
        since = hours_between(state.woke_at, state.at)
        inertia = INERTIA_POINTS * phys.inertia_scale * state.wake_depth * math.exp(-since / INERTIA_TAU_H)
    if state.boost_at is not None:
        boost = state.boost * math.exp(-hours_between(state.boost_at, state.at) / NAP_BOOST_TAU_H)
    value = (
        BASE
        + K_CIRCADIAN * circadian(clock_h(state.at), phys.mid_sleep_h)
        - K_PRESSURE * (state.pressure + phys.fatigue)
        - inertia
        + boost
        - min(DEBT_POINTS_PER_H * phys.debt_h, MAX_DEBT_POINTS)
    )
    return round(_clamp(value, 0, 100), 1)


def simulate(state: BodyState, blocks: list[SleepBlock], times: list[datetime]) -> list[BodyState]:
    blocks = sorted(blocks, key=lambda b: b.start)
    out = []
    for t in times:
        state = advance(state, blocks, t)
        out.append(state)
    return out


def time_grid(start: datetime, end: datetime, step_min: int = 15) -> list[datetime]:
    n = max(0, int(hours_between(start, end) * 60 // step_min))
    return [start + timedelta(minutes=step_min * i) for i in range(n + 1)]


# --------------------------------------------------------------------------- body clock & travel


def time_zone_shift_h(travel: TravelPlan) -> float:
    """Hours the destination clock is ahead of home (negative = behind), from zoneinfo."""
    home = travel.departure.replace(tzinfo=ZoneInfo(travel.home_tz)).utcoffset()
    away = travel.arrival.replace(tzinfo=ZoneInfo(travel.destination_tz)).utcoffset()
    diff = (away - home).total_seconds() / 3600
    return ((diff + 12) % 24) - 12


def adjust_rate_h_per_day(shift_h: float, age_band: AgeBand) -> float:
    rate = EAST_H_PER_DAY if shift_h > 0 else WEST_H_PER_DAY
    return rate * (OLDER_JET_LAG_FACTOR if age_band == AgeBand.OLDER else 1.0)


def pre_trip_shift_h(shift_h: float) -> float:
    """How much of the shift the 3-day pre-trip plan covers (only for 3+ h)."""
    if abs(shift_h) < JET_LAG_MIN_H:
        return 0.0
    return min(abs(shift_h), PRE_TRIP_DAYS * PRE_TRIP_H_PER_DAY)


def pre_trip_progress_h(travel: TravelPlan, when: datetime) -> float:
    """How far the pre-trip plan has moved the body clock by ``when`` (home time),
    assuming it's followed: 1 h a day over the 3 days before departure."""
    total = pre_trip_shift_h(time_zone_shift_h(travel))
    if when >= travel.departure:
        return total
    start = datetime.combine(travel.departure.date() - timedelta(days=PRE_TRIP_DAYS), time(12))
    return _clamp(hours_between(start, when) / 24 * PRE_TRIP_H_PER_DAY, 0.0, total)


def jet_lag_remaining_h(travel: TravelPlan, age_band: AgeBand, when: datetime) -> float:
    """Hours the body clock is still out of sync at ``when`` (destination local time)."""
    if when < travel.arrival:
        return 0.0
    shift = time_zone_shift_h(travel)
    left = abs(shift) - pre_trip_shift_h(shift)
    days = hours_between(travel.arrival, when) / 24
    return max(0.0, left - adjust_rate_h_per_day(shift, age_band) * days)


def body_clock_phase(data: UserData, now: datetime, personal: learning.Personal) -> float:
    """Mid-sleep clock time the body clock is on right now (0-24 h)."""
    mid = personal.body_clock.mean
    travel = data.profile.travel
    if travel is not None:
        shift = time_zone_shift_h(travel)
        if now >= travel.arrival:  # local time now: the clock still lags (east) or leads (west)
            mid += math.copysign(jet_lag_remaining_h(travel, data.profile.age_band, now), shift)
        else:  # still at home: the pre-trip plan is pulling it towards the destination
            mid -= math.copysign(pre_trip_progress_h(travel, now), shift)
    return mid % 24


def social_jet_lag_h(data: UserData, now: datetime) -> float | None:
    """This week's social jet lag: the gap between your sleep midpoint on free days
    and on work days (Roenneberg; Brief §7). In holiday mode every day is free, so
    it's the gap between your holiday midpoint and your term-time one."""
    recent = [s for s in nights(data.sessions, now) if s.end >= now - timedelta(days=7)]
    if not recent:
        return None
    mids = [(learning.mid_sleep_since_noon(s.start, s.end), s.end.weekday() >= 5) for s in recent]
    if data.profile.mode == Mode.HOLIDAY:
        gap = median(m for m, _ in mids) - learning.usual_mid_since_noon(data.profile)
        return round(abs(gap), 2)
    free = [m for m, is_free in mids if is_free]
    work = [m for m, is_free in mids if not is_free]
    if not free or not work:
        return None
    return round(abs(median(free) - median(work)), 2)


# --------------------------------------------------------------------------- metrics


def sleep_debt_h(sessions: list[SleepSession], need_h: float, now: datetime) -> float:
    """Running sleep debt over the last 14 nights: need minus actual sleep, added up
    night by night, never below zero; naps count (Brief §2). A sleep 'day' runs
    2 p.m. to 2 p.m., so a morning nap counts towards the night before it."""
    totals: dict[date, float] = {}
    night_keys = set()
    for s in sessions:
        if s.end > now:
            continue
        key = (s.end - timedelta(hours=14)).date()
        totals[key] = totals.get(key, 0.0) + s.hours_asleep
        if not s.is_nap:
            night_keys.add(key)
    debt = 0.0
    for key in sorted(night_keys)[-DEBT_NIGHTS:]:
        debt = max(0.0, debt + need_h - totals[key])
    return round(debt, 2)


def hrv_vs_baseline(sessions: list[SleepSession], now: datetime) -> tuple[float, float] | None:
    """(z-score, % difference) of last night's HRV against your previous 7 nights.
    HRV is only ever compared with your own baseline, never with other people (Brief §9)."""
    ns = [s for s in nights(sessions, now) if s.hrv_ms]
    if len(ns) < 4 or now - ns[-1].end > timedelta(hours=24):
        return None
    last, base = ns[-1], ns[-1 - HRV_BASELINE_NIGHTS : -1]
    logs = [math.log(s.hrv_ms) for s in base]
    mu = sum(logs) / len(logs)
    sd = max(pstdev(logs), 0.05)
    z = (math.log(last.hrv_ms) - mu) / sd
    return round(z, 2), round((last.hrv_ms / math.exp(mu) - 1) * 100, 1)


def sleep_score(session: SleepSession, need_h: float) -> int:
    """0-100 from sleep time vs need (70 %) and efficiency (30 %), the only watch
    numbers the model trusts (Brief §9). 85 %+ efficiency is the healthy range."""
    duration = _clamp(session.hours_asleep / need_h)
    efficiency = _clamp((session.efficiency - 0.70) / 0.25)
    return round(100 * (0.7 * duration + 0.3 * efficiency))


def readiness(last: SleepSession | None, need_h: float, debt_h: float, hrv_z: float | None) -> int:
    sleep = sleep_score(last, need_h) / 100 if last else 0.5
    debt = _clamp(1 - debt_h / 10)
    hrv = _clamp(0.5 + 0.2 * hrv_z) if hrv_z is not None else 0.5
    return round(100 * (0.5 * sleep + 0.3 * debt + 0.2 * hrv))


CATEGORY_LOAD = {
    EventCategory.EXAM: 1.0,
    EventCategory.WORK: 0.6,
    EventCategory.STUDY: 0.6,
    EventCategory.EXERCISE: 0.6,
    EventCategory.LECTURE: 0.5,
    EventCategory.TRAVEL: 0.5,
    EventCategory.OTHER: 0.4,
    EventCategory.SOCIAL: 0.3,
    EventCategory.REST: 0.0,
}


def day_strain(events: list[CalendarEvent], day: date) -> float:
    """0-1: how demanding a day's calendar is (8 h of exams = 1)."""
    load = sum(
        e.minutes * (e.stress if e.stress is not None else CATEGORY_LOAD[e.category])
        for e in events
        if e.start.date() == day
    )
    return round(_clamp(load / 480), 2)


def night_stats(s: SleepSession, need_h: float) -> NightStats:
    return NightStats(
        morning=s.end.date(), bedtime=s.start, wake=s.end, hours_asleep=round(s.hours_asleep, 2),
        efficiency=s.efficiency, sleep_score=sleep_score(s, need_h), hrv_ms=s.hrv_ms,
        deep_pct=s.deep_pct, rem_pct=s.rem_pct, rating=s.rating,
    )


def nightly_stats(sessions: list[SleepSession], need_h: float, now: datetime) -> list[NightStats]:
    """One row per night for the sleep-history page (stages included for display)."""
    return [night_stats(s, need_h) for s in nights(sessions, now)]


GP_NIGHTS = 14  # our threshold (Brief §10)


def gp_card(data: UserData, now: datetime) -> Card | None:
    """A calm 'consider talking to your GP' card after two weeks of very poor
    check-ins (rating 1-2). Never a diagnosis, never blocking (Brief §10)."""
    rated = [s for s in nights(data.sessions, now) if s.rating is not None][-GP_NIGHTS:]
    if len(rated) < GP_NIGHTS or rated[0].end < now - timedelta(days=GP_NIGHTS + 1):
        return None
    if any(s.rating > 2 for s in rated):
        return None
    points = [
        "Trouble sleeping for months, habits haven't helped, or it's hard to cope with daily life (NHS).",
        "Loud snoring, pauses in breathing or gasping at night, or being very sleepy during the day (NHS inform).",
        "Needing to nap much more than usual, especially feeling tired right after waking (Mayo Clinic).",
    ]
    if data.profile.age_band == AgeBand.OLDER:
        points.append("Feeling tired and unable to do your usual activities for more than 2-3 weeks (NIA).")
    return Card(
        title="Consider talking to your GP",
        body=(
            "Your check-ins have shown very poor sleep for two weeks in a row. Doze can help you "
            "plan sleep, but it can't diagnose anything. A GP can help, especially if any of these "
            "sound familiar:"
        ),
        points=points,
    )


# --------------------------------------------------------------------------- the fitted model


class PersonModel:
    """The sleep model fitted to one person at one moment. Build it once per request."""

    def __init__(self, data: UserData, now: datetime):
        self.data, self.now = data, now
        self.personal = learning.personalise(data, now)
        self.need_h = self.personal.need_h
        self.debt_h = sleep_debt_h(data.sessions, self.need_h, now)
        self.hrv = hrv_vs_baseline(data.sessions, now)
        fatigue = HRV_FATIGUE_PER_SD * _clamp(-self.hrv[0] - 0.5, 0, 2.5) if self.hrv else 0.0
        self.phys = Physiology(
            mid_sleep_h=body_clock_phase(data, now, self.personal),
            fatigue=fatigue,
            debt_h=self.debt_h,
            inertia_scale=self.personal.inertia_scale,
        )
        past = nights(data.sessions, now)
        self.last_night = past[-1] if past and now - past[-1].end <= timedelta(hours=24) else None
        effs = [s.efficiency for s in past[-7:]]
        self.efficiency = _clamp(median(effs), 0.75, 0.98) if effs else TYPICAL_EFFICIENCY
        self.blocks = blocks_from_sessions([s for s in data.sessions if s.start < now])
        if not past or now - past[-1].end > timedelta(hours=30):
            self.blocks = sorted(self.blocks + [self._assumed_last_night()], key=lambda b: b.start)
        self.origin = BodyState(at=self.blocks[0].start, pressure=S_START)
        self.state = advance(self.origin, self.blocks, now)

    def _assumed_last_night(self) -> SleepBlock:
        """No recent data: assume the usual schedule (so new users still get a curve)."""
        p = self.data.profile
        wake = datetime.combine(self.now.date(), p.usual_wake)
        if wake > self.now:
            wake -= timedelta(days=1)
        bed = datetime.combine(wake.date(), p.usual_bedtime)
        if bed >= wake:
            bed -= timedelta(days=1)
        return SleepBlock(bed, wake, TYPICAL_EFFICIENCY)

    @property
    def alertness_now(self) -> float | None:
        return alertness(self.state, self.phys)

    def state_at(self, when: datetime) -> BodyState:
        """Predicted state at ``when`` assuming no more sleep until then."""
        if when >= self.now:
            return advance(self.state, [], when)
        return advance(self.origin, self.blocks, when)

    def curve(
        self,
        start: datetime,
        end: datetime,
        extra: list[SleepBlock] = (),
        step_min: int = 15,
        from_state: BodyState | None = None,
    ) -> list[AlertnessPoint]:
        """Alertness over time. Past points replay real history; ``extra`` adds
        hypothetical sleep (a nap, tonight's bedtime) for what-if curves."""
        if from_state is not None:
            states = simulate(from_state, list(extra), time_grid(max(start, from_state.at), end, step_min))
        else:
            states = simulate(self.origin, self.blocks + list(extra), time_grid(start, end, step_min))
        return [AlertnessPoint(at=s.at, alertness=alertness(s, self.phys)) for s in states]
