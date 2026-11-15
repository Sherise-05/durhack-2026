"""Shared data contract for Doze: every service and page speaks these models.

Change them as a team. The database layer (tiger_data.py) returns the input
models, the maths (sleep_model.py and friends) consumes them, and the pages
render the output models (all JSON-serialisable via ``.model_dump(mode="json")``).

Conventions
-----------
* Datetimes are **naive local time** internally, in the demo's explicit
  Europe/London zone. Aware inputs retain their instant via the repeated-hour
  fold; JSON spans include timezone offsets so that fold survives storage.
  Travel fields are the exception: departure uses home_tz and arrival uses
  destination_tz. The integration must supply now and sleep/calendar records
  in a consistent local clock; the demo default is not a global
  multi-timezone ingestion strategy.
* Fractions are 0-1 (``efficiency=0.9`` means 90 %).
* Durations are minutes unless the name ends in ``_h`` (hours).
* "Brief §N" refers to the Doze Sleep Science Brief (Nov 2026).
"""

from __future__ import annotations

from datetime import date, datetime, time
from enum import Enum
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationInfo, field_serializer, field_validator, model_validator
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.time_utils import hours_between, instant, local_datetime, local_isoformat


def _naive_local(value: datetime) -> datetime:
    return local_datetime(value)


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
    model_config = ConfigDict(allow_inf_nan=False)
    start: LocalDatetime
    end: LocalDatetime

    @model_validator(mode="after")
    def _end_after_start(self):
        if instant(self.end) <= instant(self.start):
            raise ValueError("end must be after start")
        return self

    @property
    def minutes(self) -> float:
        return hours_between(self.start, self.end) * 60

    @field_serializer("start", "end", when_used="json")
    def _serialise_clock(self, value: datetime) -> str:
        return local_isoformat(value)


class SleepSession(_Span):
    """One sleep from the watch, either a night or a nap. start = into bed, end = up.

    The model only uses sleep time and efficiency, plus HRV against a 7-day
    personal baseline. Sleep stages and awakenings are display-only (Brief §9).
    """

    is_nap: bool = False
    episode_id: str | None = Field(None, description="stable ID linking fragments of the same primary sleep episode")
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

    @model_validator(mode="after")
    def _supported_duration(self):
        if self.minutes > 24 * 60:
            raise ValueError("split sleep records longer than 24 hours into separate sessions")
        return self


def normalise_sessions(sessions: list[SleepSession]) -> list[SleepSession]:
    """Upsert identical spans (last record wins); reject ambiguous overlaps.

    All consumers use the same records. Overlaps need resolving at ingestion,
    rather than guessing which duration, efficiency or check-in to trust.
    """
    unique = {(instant(s.start), instant(s.end)): s for s in sessions}
    ordered = sorted(unique.values(), key=lambda s: instant(s.start))
    for previous, current in zip(ordered, ordered[1:]):
        if instant(current.start) < instant(previous.end):
            raise ValueError("sleep sessions overlap; reconcile the source records first")
    return ordered


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
    departure: datetime  # home local time
    arrival: datetime  # destination local time
    planned_from: date | None = None  # None preserves legacy/demo full preparation
    travel_min: int = Field(0, ge=0, description="ground travel to the departure location")
    pre_departure_buffer_min: int = Field(0, ge=0, description="additional check-in/security time before departure")

    @field_validator("home_tz", "destination_tz")
    @classmethod
    def _valid_zone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("use a valid IANA timezone name") from exc
        return value

    @field_validator("departure", "arrival")
    @classmethod
    def _travel_local(cls, value: datetime, info: ValidationInfo) -> datetime:
        zone_name = "home_tz" if info.field_name == "departure" else "destination_tz"
        zone = info.data.get(zone_name, "Europe/London")
        return local_datetime(value, zone)

    @model_validator(mode="after")
    def _arrival_after_departure(self):
        if instant(self.arrival, self.destination_tz) <= instant(self.departure, self.home_tz):
            raise ValueError("arrival must be after departure as an actual instant")
        return self

    @field_serializer("departure", "arrival", when_used="json")
    def _serialise_travel_clock(self, value: datetime, info) -> str:
        zone = self.home_tz if info.field_name == "departure" else self.destination_tz
        return local_isoformat(value, zone)


class UserProfile(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
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

    @field_validator("usual_bedtime", "usual_wake")
    @classmethod
    def _local_clock_only(cls, value: time) -> time:
        if value.tzinfo is not None:
            raise ValueError("use a local clock time without a timezone offset")
        return value


class UserData(BaseModel):
    """Everything we know about one user: what tiger_data / personas hand to the maths."""

    profile: UserProfile
    sessions: list[SleepSession] = Field(default_factory=list)
    events: list[CalendarEvent] = Field(default_factory=list)

    @field_validator("sessions")
    @classmethod
    def _canonical_sleep(cls, value: list[SleepSession]) -> list[SleepSession]:
        return normalise_sessions(value)


# --------------------------------------------------------------------------- outputs


class _ClockOutput(BaseModel):
    @field_serializer("*", mode="wrap", when_used="json", check_fields=False)
    def _serialise_datetime(self, value, handler):
        return local_isoformat(value) if isinstance(value, datetime) else handler(value)


class AlertnessPoint(_ClockOutput):
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


class NightStats(_ClockOutput):
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


class Snapshot(_ClockOutput):
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


class NapRecommendation(_ClockOutput):
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


class BedtimeRecommendation(_ClockOutput):
    feasible: bool = True
    in_progress: bool = False
    remaining_sleep_h: float | None = None  # expected sleep from now; sleep_h is total for this night
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


class EventForecast(_ClockOutput):
    title: str
    start: datetime
    alertness: float  # mean predicted alertness during the event


class WhatIf(_ClockOutput):
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
