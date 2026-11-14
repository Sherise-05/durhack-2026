"""Shared data contract for Doze: every service and page speaks these models.

Change them as a team. The database layer (tiger_data.py) returns the input
models, the maths (sleep_model.py and friends) consumes them, and the pages
render the output models (all JSON-serialisable via ``.model_dump(mode="json")``).

Conventions
-----------
* Datetimes are **naive local time**. Timezone-aware values (e.g. RFC 3339
  strings from the Google Calendar API) are converted to the server's local
  time on the way in, so naive and aware datetimes can never get mixed.
* Fractions are 0-1 (``efficiency=0.9`` means 90 %).
* Durations are minutes unless the name ends in ``_h`` (hours).
* "Brief §N" refers to the Doze Sleep Science Brief (Nov 2026).
"""

from __future__ import annotations

from datetime import date, datetime, time
from enum import Enum
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, Field, model_validator


def _naive_local(value: datetime) -> datetime:
    return value.astimezone().replace(tzinfo=None) if value.tzinfo else value


LocalDatetime = Annotated[datetime, AfterValidator(_naive_local)]


# --------------------------------------------------------------------------- enums


class Mode(str, Enum):
    NORMAL = "normal"
    EXAM = "exam"  # switches on automatically when an exam is <= 2 weeks away (Brief §5)
    HOLIDAY = "holiday"  # lie-ins allowed, wake time kept near term time (Brief §7)
    TRAVEL = "travel"  # crossing time zones: jet-lag plan (Brief §6)


class Tiredness(str, Enum):
    """What the user tells us when they press the nap button."""

    FINE = "fine"
    TIRED = "tired"
    VERY_TIRED = "very_tired"


class AgeBand(str, Enum):
    """Picked at onboarding; sets the default sleep need and body-clock offset (Brief §11)."""

    TEEN = "13-17"
    YOUNG_ADULT = "18-25"
    ADULT = "26-64"
    OLDER = "65+"


class Chronotype(str, Enum):
    """Early / neutral / late setting; shifts the body-clock phase (Brief §4)."""

    EARLY = "early"
    NEUTRAL = "neutral"  # shown in onboarding as "Not sure"; the age-band average is used instead
    LATE = "late"


class LifeStage(str, Enum):
    """Opt-in toggles. They change tips only, never the model's numbers (Brief §11)."""

    CYCLE = "cycle"
    PREGNANCY = "pregnancy"
    MENOPAUSE = "menopause"


class EventCategory(str, Enum):
    LECTURE = "lecture"
    EXAM = "exam"
    STUDY = "study"
    WORK = "work"
    EXERCISE = "exercise"
    SOCIAL = "social"
    TRAVEL = "travel"  # includes driving: needs the 20-min grogginess buffer (Brief §3)
    REST = "rest"
    OTHER = "other"


# --------------------------------------------------------------------------- inputs


class _Span(BaseModel):
    start: LocalDatetime
    end: LocalDatetime

    @model_validator(mode="after")
    def _end_after_start(self):
        if self.end <= self.start:
            raise ValueError("end must be after start")
        return self

    @property
    def minutes(self) -> float:
        return (self.end - self.start).total_seconds() / 60


class SleepSession(_Span):
    """One sleep from the watch, either a night or a nap. start = into bed, end = up.

    The model only uses sleep time and efficiency, plus HRV against a 7-day
    personal baseline. Sleep stages and awakenings are display-only (Brief §9).
    """

    is_nap: bool = False
    efficiency: float = Field(0.9, ge=0, le=1, description="time asleep / time in bed")
    latency_min: float | None = Field(None, ge=0, description="minutes to fall asleep")
    hrv_ms: float | None = Field(None, gt=0, description="overnight HRV (RMSSD)")
    deep_pct: float | None = Field(None, ge=0, le=1, description="display only")
    rem_pct: float | None = Field(None, ge=0, le=1, description="display only")
    awakenings: int | None = Field(None, ge=0, description="display only")
    resting_hr: float | None = Field(None, gt=0, description="display only")
    rating: int | None = Field(
        None, ge=1, le=5, description="morning check-in: 1 = very poor ... 5 = fully refreshed"
    )

    @property
    def hours_asleep(self) -> float:
        return self.minutes / 60 * self.efficiency


class CalendarEvent(_Span):
    title: str
    category: EventCategory = EventCategory.OTHER
    location: str | None = None
    travel_min: int = Field(0, ge=0, description="time needed to get there")
    stress: float | None = Field(
        None, ge=0, le=1, description="AI-estimated load; overrides the category default"
    )


class TravelPlan(BaseModel):
    home_tz: str = "Europe/London"  # IANA names; the difference comes from zoneinfo
    destination_tz: str
    departure: LocalDatetime  # home local time
    arrival: LocalDatetime  # destination local time


class UserProfile(BaseModel):
    name: str
    age_band: AgeBand = AgeBand.YOUNG_ADULT
    gender: str | None = Field(
        None, description="optional, self-described; the maths never reads it (Brief §11)"
    )
    life_stages: list[LifeStage] = Field(default_factory=list)
    sleep_need_h: float | None = Field(
        None, ge=7, le=10, description="None = age-band default; clamped to the band's healthy range"
    )
    chronotype: Chronotype = Chronotype.NEUTRAL
    usual_bedtime: time = time(23, 30)  # the normal (term-time / work-week) schedule
    usual_wake: time = time(7, 30)
    sleep_latency_min: float = Field(15, ge=0, le=90)
    morning_routine_min: int = Field(45, ge=0, description="alarm -> out of the door")
    mode: Mode = Mode.NORMAL
    travel: TravelPlan | None = None  # travel mode
    term_starts: date | None = None  # holiday mode: when the normal schedule resumes


class UserData(BaseModel):
    """Everything we know about one user: what tiger_data / personas hand to the maths."""

    profile: UserProfile
    sessions: list[SleepSession] = Field(default_factory=list)
    events: list[CalendarEvent] = Field(default_factory=list)


# --------------------------------------------------------------------------- outputs


class AlertnessPoint(BaseModel):
    at: datetime
    alertness: float | None = None  # 0-100; None while asleep (Chart.js draws a gap)


class Belief(BaseModel):
    """A Bayesian posterior over one number, summarised for the UI."""

    mean: float
    sd: float
    low: float  # 10th percentile
    high: float  # 90th percentile
    n: int  # observations that went into it
    grid: list[tuple[float, float]] = Field(default_factory=list)  # (value, probability) to chart


class Card(BaseModel):
    """A calm prompt card, e.g. 'consider talking to your GP' (Brief §10)."""

    title: str
    body: str
    points: list[str] = Field(default_factory=list)


class NightStats(BaseModel):
    morning: date  # the day you woke up
    bedtime: datetime
    wake: datetime
    hours_asleep: float
    efficiency: float
    sleep_score: int  # 0-100, from sleep time vs need and efficiency only
    hrv_ms: float | None = None
    deep_pct: float | None = None  # display only
    rem_pct: float | None = None  # display only
    rating: int | None = None


class Snapshot(BaseModel):
    """The dashboard's headline numbers right now."""

    now: datetime
    mode: Mode  # the *effective* mode (exam mode can switch itself on)
    mode_reason: str | None = None
    readiness: int  # 0-100
    alertness_now: float | None
    sleep_need_h: float
    sleep_debt_h: float
    last_night: NightStats | None
    hrv_vs_baseline_pct: float | None  # vs your own 7-day baseline
    social_jet_lag_h: float | None  # this week (Brief §7)
    day_strain: float  # 0-1, how demanding today's calendar is
    tips: list[str] = Field(default_factory=list)
    gp_card: Card | None = None


class NapRecommendation(BaseModel):
    should_nap: bool
    kind: Literal["power", "full_cycle", "none"]  # 10/20 min, or 90 min (Brief §3)
    lie_down: datetime
    alarm: datetime | None
    sleep_min: int
    alertness_now: float | None
    alertness_after: float | None  # predicted 30 min after the alarm
    tonight_cost_pct: float  # share of tonight's sleep pressure the nap uses up
    headline: str
    reasons: list[str]
    warnings: list[str] = Field(default_factory=list)
    with_nap: list[AlertnessPoint] = Field(default_factory=list)
    without_nap: list[AlertnessPoint] = Field(default_factory=list)


class BedtimeRecommendation(BaseModel):
    wind_down: datetime
    bedtime: datetime  # lights out
    alarm: datetime
    sleep_h: float  # expected time actually asleep
    need_h: float
    debt_h: float
    cycles: float  # ~90 min sleep cycles, for display
    first_event: str | None
    headline: str
    reasons: list[str]
    warnings: list[str] = Field(default_factory=list)


class EventForecast(BaseModel):
    title: str
    start: datetime
    alertness: float  # mean predicted alertness during the event


class WhatIf(BaseModel):
    """What-if simulator: 'if I sleep from bedtime to alarm, how will tomorrow go?'"""

    bedtime: datetime
    alarm: datetime
    sleep_h: float
    curve: list[AlertnessPoint]
    events: list[EventForecast]
    warnings: list[str] = Field(default_factory=list)


class ShiftDay(BaseModel):
    """One night of a shift plan: bed on the evening of ``day``, wake the next morning."""

    day: date
    where: Literal["home", "destination"]
    bedtime: time
    wake: time  # the morning after ``day``
    light: Literal["morning", "evening"] | None = None
    seek_light: tuple[time, time] | None = None
    body_clock_off_h: float  # hours your body clock is still out of sync with local time
    note: str = ""


class ShiftPlan(BaseModel):
    """A day-by-day plan for moving your body clock (jet lag, or back to term time)."""

    kind: Literal["jet_lag", "back_to_term"]
    direction: Literal["advance", "delay", "none"]  # advance = earlier, delay = later
    shift_h: float
    days_to_adjust: int
    summary: str
    days: list[ShiftDay] = Field(default_factory=list)
    tips: list[str] = Field(default_factory=list)
