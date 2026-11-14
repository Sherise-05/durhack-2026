"""Demo personas from data/personas.json.

Every date in the file is relative to *today*, so "last night" is always last
night during a live demo: ``"night": -1`` is the night that started yesterday
evening, ``"day": 0`` is today. Bedtimes before noon belong to the morning
after the night started. Person 2 can also use this to seed Tiger Data.

    from app.services.personas import load_persona
    data = load_persona("maya")          # -> UserData
"""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from pathlib import Path

from app.schemas import CalendarEvent, SleepSession, TravelPlan, UserData, UserProfile

PERSONAS_PATH = Path(__file__).resolve().parents[2] / "data" / "personas.json"


def _clock(hhmm: str) -> time:
    return time.fromisoformat(hhmm)


def _at(today: date, day: int, hhmm: str) -> datetime:
    return datetime.combine(today + timedelta(days=day), _clock(hhmm))


def _night(today: date, n: dict) -> SleepSession:
    evening = today + timedelta(days=n["night"])
    bed = _clock(n["bed"])
    start = datetime.combine(evening + timedelta(days=1) if bed.hour < 12 else evening, bed)
    end = datetime.combine(evening + timedelta(days=1), _clock(n["wake"]))
    fields = {k: v for k, v in n.items() if k not in ("night", "bed", "wake")}
    return SleepSession(start=start, end=end, **fields)


def _nap(today: date, n: dict) -> SleepSession:
    start = _at(today, n["day"], n["start"])
    fields = {k: v for k, v in n.items() if k not in ("day", "start", "minutes")}
    return SleepSession(start=start, end=start + timedelta(minutes=n["minutes"]), is_nap=True, **fields)


def _event(today: date, e: dict) -> CalendarEvent:
    start, end = _at(today, e["day"], e["start"]), _at(today, e["day"], e["end"])
    if end <= start:
        end += timedelta(days=1)
    fields = {k: v for k, v in e.items() if k not in ("day", "start", "end")}
    return CalendarEvent(start=start, end=end, **fields)


def _profile(today: date, raw: dict) -> UserProfile:
    raw = dict(raw)
    if travel := raw.pop("travel", None):
        raw["travel"] = TravelPlan(
            home_tz=travel["home_tz"],
            destination_tz=travel["destination_tz"],
            departure=_at(today, travel["depart_day"], travel["depart"]),
            arrival=_at(today, travel["arrive_day"], travel["arrive"]),
        )
    if (term := raw.pop("term_starts_day", None)) is not None:
        raw["term_starts"] = today + timedelta(days=term)
    return UserProfile(**raw)


@lru_cache(maxsize=8)
def _raw(path: Path) -> dict:
    return json.loads(path.read_text())


def load_personas(today: date | None = None, path: Path = PERSONAS_PATH) -> dict[str, UserData]:
    today = today or date.today()
    out = {}
    for key, p in _raw(path).items():
        if key.startswith("_"):
            continue
        sessions = [_night(today, n) for n in p["nights"]] + [_nap(today, n) for n in p.get("naps", [])]
        out[key] = UserData(
            profile=_profile(today, p["profile"]),
            sessions=sorted(sessions, key=lambda s: s.start),
            events=[_event(today, e) for e in p.get("events", [])],
        )
    return out


def load_persona(key: str, today: date | None = None) -> UserData:
    return load_personas(today)[key]


def persona_blurbs() -> dict[str, str]:
    return {k: v.get("blurb", "") for k, v in _raw(PERSONAS_PATH).items() if not k.startswith("_")}
