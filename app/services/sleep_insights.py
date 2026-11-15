"""Insights for the My sleep page: patterns in the nights the user has logged.

Pure arithmetic over completed primary nights (no AI). Thresholds marked
"our choice" are display decisions, not research cut-offs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from statistics import mean, pstdev

from app.schemas import NightStats

WINDOW_DAYS = 14
RECENT_DAYS = 7
REGULAR_SD_MIN = 30  # our choice: within ~30 min night to night counts as very regular
FAIRLY_REGULAR_SD_MIN = 60  # our choice
SOCIAL_JET_LAG_MIN = 60  # our choice: flag a 1 h+ weekend shift
MIN_NIGHTS_FOR_PATTERNS = 3


@dataclass
class Insight:
    title: str
    body: str
    tone: str = "info"  # "good" | "warn" | "info"


@dataclass
class SleepInsights:
    nights_logged: int
    recent_avg_h: float | None
    recent_vs_need_h: float | None
    regularity: str | None
    wake_sd_min: float | None
    bed_sd_min: float | None
    social_jet_lag_min: float | None
    hours_chart: dict
    window_chart: dict
    insights: list[Insight] = field(default_factory=list)


def _fmt(hours: float) -> str:
    minutes = round(abs(hours) * 60)
    h, m = divmod(minutes, 60)
    return f"{h}h {m:02d}m" if h else f"{m} min"


def _since_noon(t: datetime) -> float:
    """Clock minutes measured from noon, so 22:00 and 01:00 sit on one line."""
    return ((t.hour - 12) % 24) * 60 + t.minute


def _since_midnight_shifted(t: datetime) -> float:
    """Wake clock minutes measured from 18:00 the day before (wake times rarely wrap)."""
    return ((t.hour - 18) % 24) * 60 + t.minute


def _clock(minutes_from_noon: float) -> str:
    total = round(minutes_from_noon + 12 * 60) % (24 * 60)
    return f"{total // 60:02d}:{total % 60:02d}"


def build(nights: list[NightStats], need_h: float, debt_h: float, today: date) -> SleepInsights:
    start = today - timedelta(days=WINDOW_DAYS - 1)
    window = sorted((n for n in nights if start <= n.morning <= today), key=lambda n: n.morning)
    by_day: dict[date, float] = {}
    for night in window:
        by_day[night.morning] = by_day.get(night.morning, 0.0) + night.hours_asleep

    days = [start + timedelta(days=i) for i in range(WINDOW_DAYS)]
    hours_chart = {
        "points": [{"label": d.strftime("%d %b"), "value": round(by_day[d], 2) if d in by_day else None} for d in days],
        "reference": round(need_h, 2),
    }
    window_chart = {
        "points": [
            {"label": d.strftime("%a %d"),
             "start": next((_since_noon(n.bedtime) for n in window if n.morning == d), None),
             "end": next((_since_noon(n.wake) for n in window if n.morning == d), None)}
            for d in days
        ],
    }

    recent = [by_day[d] for d in days[-RECENT_DAYS:] if d in by_day]
    recent_avg = mean(recent) if recent else None
    vs_need = recent_avg - need_h if recent_avg is not None else None

    wake_sd = bed_sd = sjl = None
    regularity = None
    if len(window) >= MIN_NIGHTS_FOR_PATTERNS:
        wake_sd = pstdev(_since_midnight_shifted(n.wake) for n in window)
        bed_sd = pstdev(_since_noon(n.bedtime) for n in window)
        worst = max(wake_sd, bed_sd)
        regularity = ("Very regular" if worst <= REGULAR_SD_MIN
                      else "Fairly regular" if worst <= FAIRLY_REGULAR_SD_MIN else "Irregular")
        mid = {n.morning: (_since_noon(n.bedtime) + _since_noon(n.wake)) / 2 for n in window}
        weekend = [m for d, m in mid.items() if d.weekday() >= 5]  # woke Sat/Sun
        weekday = [m for d, m in mid.items() if d.weekday() < 5]
        if weekend and weekday:
            sjl = mean(weekend) - mean(weekday)

    insights: list[Insight] = []
    if recent_avg is not None and vs_need is not None:
        if vs_need <= -0.5:
            insights.append(Insight(
                "Running short",
                f"Over your last {len(recent)} logged night{'s' if len(recent) != 1 else ''} you averaged "
                f"{_fmt(recent_avg)}, {_fmt(vs_need)} under your {_fmt(need_h)} need.", "warn"))
        elif vs_need >= -0.25:
            insights.append(Insight(
                "Meeting your need",
                f"You averaged {_fmt(recent_avg)} recently, in line with your {_fmt(need_h)} need.", "good"))
    if debt_h >= 1:
        insights.append(Insight(
            "Sleep debt",
            f"You're carrying about {_fmt(debt_h)} of sleep debt. Doze plans a little extra sleep each night "
            "to win it back gradually rather than all at once.", "warn"))
    if wake_sd is not None:
        if max(wake_sd, bed_sd) <= REGULAR_SD_MIN:
            insights.append(Insight(
                "Steady rhythm",
                f"Your bed and wake times stay within about {round(max(wake_sd, bed_sd))} min night to night. "
                "Students with regular sleep had better grades (Phillips et al. 2017).", "good"))
        elif wake_sd > FAIRLY_REGULAR_SD_MIN:
            insights.append(Insight(
                "Wake time moves around",
                f"Your wake-up time varies by about {_fmt(wake_sd / 60)} night to night. "
                "A steadier wake time is the easiest way to steady your body clock.", "warn"))
    if sjl is not None and abs(sjl) >= SOCIAL_JET_LAG_MIN:
        insights.append(Insight(
            "Weekend shift",
            f"On weekends your sleep sits about {_fmt(abs(sjl) / 60)} {'later' if sjl > 0 else 'earlier'} than on "
            "weekdays, like flying a time zone every weekend (social jet lag).", "warn"))
    rated_good = [n.hours_asleep for n in window if n.rating in (4, 5)]
    rated_poor = [n.hours_asleep for n in window if n.rating in (1, 2)]
    if len(rated_good) >= 2 and len(rated_poor) >= 2 and mean(rated_good) - mean(rated_poor) >= 0.25:
        insights.append(Insight(
            "What feeling rested takes",
            f"Nights you rated rested averaged {_fmt(mean(rated_good))}; groggy mornings followed "
            f"{_fmt(mean(rated_poor))}. Doze uses this to learn your sleep need.", "info"))
    if window:
        longest = max(window, key=lambda n: n.hours_asleep)
        shortest = min(window, key=lambda n: n.hours_asleep)
        if len(window) >= MIN_NIGHTS_FOR_PATTERNS and longest.hours_asleep - shortest.hours_asleep >= 1:
            insights.append(Insight(
                "Range",
                f"Longest night: {_fmt(longest.hours_asleep)} ({longest.morning:%a %d %b}). "
                f"Shortest: {_fmt(shortest.hours_asleep)} ({shortest.morning:%a %d %b}).", "info"))
    if len(window) < MIN_NIGHTS_FOR_PATTERNS:
        insights.append(Insight(
            "More nights, more insight",
            f"Log {MIN_NIGHTS_FOR_PATTERNS - len(window)} more night{'s' if MIN_NIGHTS_FOR_PATTERNS - len(window) != 1 else ''} "
            "to see your rhythm, weekend shift and what feeling rested takes.", "info"))

    return SleepInsights(
        nights_logged=len(window), recent_avg_h=recent_avg, recent_vs_need_h=vs_need,
        regularity=regularity, wake_sd_min=wake_sd, bed_sd_min=bed_sd, social_jet_lag_min=sjl,
        hours_chart=hours_chart, window_chart=window_chart, insights=insights,
    )
