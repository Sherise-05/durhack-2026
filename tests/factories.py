"""Tiny builders for test data. All times are relative to a fixed Monday."""

from datetime import datetime, timedelta

from app.schemas import CalendarEvent, EventCategory, SleepSession, UserData, UserProfile

MONDAY = datetime(2026, 11, 16)


def at(day: int, hhmm: str) -> datetime:
    h, m = map(int, hhmm.split(":"))
    return MONDAY + timedelta(days=day, hours=h, minutes=m)


def night(n: int, bed: str = "22:40", wake: str = "07:30", eff: float = 0.9, hrv: float = 60.0, **kw) -> SleepSession:
    """The night starting on the evening of day ``n`` (a bedtime before noon is after midnight)."""
    start = at(n + 1 if int(bed[:2]) < 12 else n, bed)
    return SleepSession(start=start, end=at(n + 1, wake), efficiency=eff, hrv_ms=hrv, **kw)


def nap(day: int, start: str, minutes: int, eff: float = 0.9, **kw) -> SleepSession:
    s = at(day, start)
    return SleepSession(start=s, end=s + timedelta(minutes=minutes), efficiency=eff, is_nap=True, **kw)


def event(day: int, start: str, end: str, category=EventCategory.OTHER, title: str = "Thing", **kw) -> CalendarEvent:
    return CalendarEvent(title=title, start=at(day, start), end=at(day, end), category=category, **kw)


def rested_nights(n: int = 8) -> list[SleepSession]:
    """22:40-07:30 at 90 % efficiency: ~8 h asleep, no debt for an 8 h need."""
    return [night(-k) for k in range(n, 0, -1)]


def short_last_night() -> list[SleepSession]:
    return rested_nights()[:-1] + [night(-1, "02:00", "07:15", eff=0.85, hrv=45.0)]


def user(sessions=None, events=(), **profile) -> UserData:
    return UserData(
        profile=UserProfile(name="Test", **profile),
        sessions=list(rested_nights() if sessions is None else sessions),
        events=list(events),
    )
