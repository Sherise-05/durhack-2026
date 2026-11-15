"""Personalised Bayesian learning: start from research defaults, then learn from you.

Everything is recomputed from the full history on each request, so there is no
hidden model state to store. The database is the memory.

1. **Sleep need.** Prior: your age band's default (9 h for 13-17, 8 h for
   18-64, 7.5 h for 65+; Brief §11, our choice) or the value you set, kept
   inside the band's healthy range (8-10 h for teens, 7-9 h for adults; Brief
   §1, §11). Each morning check-in updates it on a grid:
       P(refreshed | slept, need) = logistic((slept - need) / 0.6 h)
   with direction-only labels: 1-2 = not enough sleep, 4-5 = enough sleep;
   3 provides no information and is excluded.
2. **Body clock.** Prior: your usual mid-sleep time plus one offset: your
   chronotype setting (+/-1 h for early / late; Brief §4, our choice) or, if
   you chose "Not sure", your age band's average instead (+30 min for 13-25,
   -30 min for 65+; Brief §11, our choice). Each night's actual mid-sleep is a
   noisy observation; a normal-normal update gives the posterior.
3. **Grogginess after naps.** One number k, your sleep-inertia sensitivity:
       P(groggy | nap with m minutes asleep) = k * typical(m)
   learned on a grid from post-nap ratings. k > 1 means naps hit you harder
   than average.

Gender is never used here, or anywhere in the maths (Brief §11).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, time

from app.schemas import AgeBand, Belief, Chronotype, SleepSession, UserData, UserProfile, normalise_sessions
from app.time_utils import instant

# --------------------------------------------------------------------------- age bands


@dataclass(frozen=True)
class BandDefaults:
    need_min_h: float
    need_h: float
    need_max_h: float
    clock_offset_h: float  # starting body-clock offset (later = positive)


AGE_BANDS: dict[AgeBand, BandDefaults] = {
    AgeBand.TEEN: BandDefaults(8.0, 9.0, 10.0, +0.5),  # NSF / AASM 8-10 h; never suggest less
    AgeBand.YOUNG_ADULT: BandDefaults(7.0, 8.0, 9.0, +0.5),  # NSF 7-9 h
    AgeBand.ADULT: BandDefaults(7.0, 8.0, 9.0, 0.0),
    AgeBand.OLDER: BandDefaults(7.0, 7.5, 9.0, -0.5),  # NSF 7-8 h, NIA 7-9 h
}
CHRONOTYPE_OFFSET_H = {Chronotype.EARLY: -1.0, Chronotype.NEUTRAL: 0.0, Chronotype.LATE: +1.0}


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _logistic(x: float) -> float:
    return 1 / (1 + math.exp(-x))


def need_range_h(profile: UserProfile) -> tuple[float, float]:
    band = AGE_BANDS[profile.age_band]
    return band.need_min_h, band.need_max_h


def prior_need_h(profile: UserProfile) -> float:
    band = AGE_BANDS[profile.age_band]
    guess = band.need_h if profile.sleep_need_h is None else profile.sleep_need_h
    return _clamp(guess, band.need_min_h, band.need_max_h)


def _grid_belief(grid: list[float], log_post: list[float], n: int, digits: int = 2) -> Belief:
    top = max(log_post)
    weights = [math.exp(lp - top) for lp in log_post]
    total = sum(weights)
    probs = [w / total for w in weights]
    mean = sum(g * p for g, p in zip(grid, probs))
    sd = math.sqrt(sum((g - mean) ** 2 * p for g, p in zip(grid, probs)))

    def quantile(q: float) -> float:
        acc = 0.0
        for g, p in zip(grid, probs):
            acc += p
            if acc >= q:
                return g
        return grid[-1]

    return Belief(
        mean=round(mean, digits),
        sd=round(sd, 3),
        low=quantile(0.1),
        high=quantile(0.9),
        n=n,
        grid=[(g, round(p, 5)) for g, p in zip(grid, probs)],
    )


# --------------------------------------------------------------------------- 1. sleep need

NEED_STEP_H = 0.05
NEED_PRIOR_SD_H = 0.5
RATING_SHARPNESS_H = 0.6


def sleep_need_belief(profile: UserProfile, sessions: list[SleepSession]) -> Belief:
    lo, hi = need_range_h(profile)
    steps = round((hi - lo) / NEED_STEP_H)
    grid = [round(lo + NEED_STEP_H * i, 2) for i in range(steps + 1)]
    prior = prior_need_h(profile)
    nights = [s for s in normalise_sessions(sessions) if not s.is_nap and s.rating in (1, 2, 4, 5) and s.hours_asleep >= 3]
    log_post = []
    for need in grid:
        lp = -0.5 * ((need - prior) / NEED_PRIOR_SD_H) ** 2
        for s in nights:
            refreshed = float(s.rating >= 4)
            p = _clamp(_logistic((s.hours_asleep - need) / RATING_SHARPNESS_H), 1e-6, 1 - 1e-6)
            lp += refreshed * math.log(p) + (1 - refreshed) * math.log(1 - p)
        log_post.append(lp)
    return _grid_belief(grid, log_post, len(nights))


# --------------------------------------------------------------------------- 2. body clock

CLOCK_PRIOR_SD_H = 0.75
CLOCK_NIGHT_SD_H = 1.25  # night-to-night scatter of mid-sleep
CLOCK_NIGHTS = 14
Z_10 = 1.2816  # 10th / 90th percentile of a normal


def since_noon_h(value: datetime | time) -> float:
    """Clock time as hours after noon (00:00 -> 12, 03:30 -> 15.5), so a night's
    times don't wrap around midnight when we average them."""
    return (value.hour + value.minute / 60 - 12) % 24


def mid_sleep_since_noon(start: datetime | time, end: datetime | time) -> float:
    a, b = since_noon_h(start), since_noon_h(end)
    if b <= a:
        b += 24
    return (a + b) / 2


def usual_mid_since_noon(profile: UserProfile) -> float:
    return mid_sleep_since_noon(profile.usual_bedtime, profile.usual_wake)


def unwrap_hours(value: float, reference: float) -> float:
    """Put a time on the nearest 24-hour branch to a common reference."""
    return reference + (value - reference + 12) % 24 - 12


def body_clock_belief(profile: UserProfile, sessions: list[SleepSession], now: datetime) -> Belief:
    """Posterior over your body clock, expressed as a mid-sleep clock time (0-24 h)."""
    offset = CHRONOTYPE_OFFSET_H[profile.chronotype]
    if profile.chronotype == Chronotype.NEUTRAL:  # "not sure": use the age-band average instead
        offset = AGE_BANDS[profile.age_band].clock_offset_h
    prior = usual_mid_since_noon(profile) + offset
    nights = sorted(
        (s for s in normalise_sessions(sessions) if not s.is_nap and instant(s.end) <= instant(now)),
        key=lambda s: instant(s.end),
    )
    obs = [unwrap_hours(mid_sleep_since_noon(s.start, s.end), prior) for s in nights[-CLOCK_NIGHTS:]]
    precision = 1 / CLOCK_PRIOR_SD_H**2 + len(obs) / CLOCK_NIGHT_SD_H**2
    mean = (prior / CLOCK_PRIOR_SD_H**2 + sum(obs) / CLOCK_NIGHT_SD_H**2) / precision
    sd = math.sqrt(1 / precision)
    to_clock = lambda h: round((h + 12) % 24, 2)  # noqa: E731
    return Belief(
        mean=to_clock(mean), sd=round(sd, 3), low=to_clock(mean - Z_10 * sd),
        high=to_clock(mean + Z_10 * sd), n=len(obs),
    )


# --------------------------------------------------------------------------- 3. nap grogginess

# Chance an average person wakes groggy after a nap with this many minutes
# asleep. The numbers are our estimate, shaped by sleep-stage timing: light
# sleep for the first ~20 min, deep sleep ~30-60 min, lighter again near the
# end of a ~90 min cycle. Brooks & Lack 2006 only tested 5-30 min naps
# (10 min best; grogginess after 30 min) and gave no probabilities (Brief §3).
TYPICAL_GROGGY = (
    (0, 0.10), (10, 0.12), (20, 0.25), (30, 0.55), (45, 0.70),
    (60, 0.60), (75, 0.40), (90, 0.25), (120, 0.40),
)
LOG_K_GRID = [math.log(0.5) + i * math.log(4) / 40 for i in range(41)]  # k from 0.5 to 2, log-spaced
K_PRIOR_SD = 0.35  # prior on log k, centred on k = 1 (average)


def typical_groggy(minutes_asleep: float) -> float:
    pts = TYPICAL_GROGGY
    if minutes_asleep <= pts[0][0]:
        return pts[0][1]
    for (m0, p0), (m1, p1) in zip(pts, pts[1:]):
        if minutes_asleep <= m1:
            return p0 + (p1 - p0) * (minutes_asleep - m0) / (m1 - m0)
    return pts[-1][1]


def inertia_belief(sessions: list[SleepSession]) -> Belief:
    """Posterior over k, worked on log k so 'twice as groggy' and 'half as groggy'
    are equally likely a priori. Reports the geometric mean (1.0 with no data)."""
    naps = [s for s in normalise_sessions(sessions) if s.is_nap and s.rating is not None]
    log_post = []
    for u in LOG_K_GRID:
        lp = -0.5 * (u / K_PRIOR_SD) ** 2
        for s in naps:
            groggy = (5 - s.rating) / 4
            p = _clamp(math.exp(u) * typical_groggy(s.hours_asleep * 60), 0.02, 0.98)
            lp += groggy * math.log(p) + (1 - groggy) * math.log(1 - p)
        log_post.append(lp)
    on_log = _grid_belief(LOG_K_GRID, log_post, len(naps), digits=4)
    return Belief(
        mean=round(math.exp(on_log.mean), 2),
        sd=round(math.exp(on_log.mean) * on_log.sd, 3),  # delta method
        low=round(math.exp(on_log.low), 2),
        high=round(math.exp(on_log.high), 2),
        n=on_log.n,
        grid=[(round(math.exp(u), 3), p) for u, p in on_log.grid],
    )


# --------------------------------------------------------------------------- bundle

LATENCY_PRIOR_WEIGHT = 5  # your setting counts as 5 nights of evidence


def latency_min(profile: UserProfile, sessions: list[SleepSession]) -> float:
    obs = [s.latency_min for s in normalise_sessions(sessions) if s.latency_min is not None and not s.is_nap]
    total = LATENCY_PRIOR_WEIGHT * profile.sleep_latency_min + sum(obs)
    return round(total / (LATENCY_PRIOR_WEIGHT + len(obs)), 1)


@dataclass(frozen=True)
class Personal:
    need: Belief
    body_clock: Belief
    inertia: Belief
    latency_min: float

    @property
    def need_h(self) -> float:
        return self.need.mean

    @property
    def inertia_scale(self) -> float:
        return self.inertia.mean


def personalise(data: UserData, now: datetime) -> Personal:
    past = [s for s in data.sessions if instant(s.end) <= instant(now)]
    return Personal(
        need=sleep_need_belief(data.profile, past),
        body_clock=body_clock_belief(data.profile, past, now),
        inertia=inertia_belief(past),
        latency_min=latency_min(data.profile, past),
    )
