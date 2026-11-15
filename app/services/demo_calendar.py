"""Fake Google-style calendar for demos, dated relative to today.

The events go through the same importer as a real Google sync, so keyword
categories, busy/free handling and display rows behave exactly as they would
with a connected calendar. An exam five days out switches Exam mode on.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.services.calendar_ingest import import_google_events
from app.time_utils import LOCAL_ZONE

# (day offset from today, start, end, title, extra Google fields)
_TEMPLATE: list[tuple[int, time, time, str, dict]] = [
    (0, time(18, 0), time(19, 0), "Gym session", {}),
    (1, time(9, 0), time(11, 0), "Maths lecture", {"location": "Lecture Theatre A"}),
    (1, time(12, 0), time(13, 0), "Lunch with friends", {}),
    (1, time(15, 0), time(17, 0), "Revision: linear algebra", {}),
    (2, time(8, 30), time(9, 30), "Driving lesson", {}),
    (2, time(10, 0), time(16, 0), "Part-time work shift", {}),
    (3, time(9, 0), time(10, 0), "Probability tutorial", {}),
    (3, time(19, 0), time(21, 0), "Society social", {"transparency": "transparent"}),
    (4, time(14, 0), time(17, 0), "Library study block", {}),
    (5, time(9, 30), time(12, 0), "Linear Algebra exam", {"location": "Sports Hall"}),
    (5, time(20, 0), time(23, 0), "Movie night", {}),
    (8, time(10, 0), time(12, 0), "Statistics exam", {"location": "Exam Hall 2"}),
]


def demo_google_events(today: date) -> list[dict]:
    zone = ZoneInfo(LOCAL_ZONE)
    events = []
    for index, (offset, start, end, title, extra) in enumerate(_TEMPLATE):
        day = today + timedelta(days=offset)
        events.append({
            "id": f"demo-{index:02d}",
            "summary": title,
            "start": {"dateTime": datetime.combine(day, start, zone).isoformat()},
            "end": {"dateTime": datetime.combine(day, end, zone).isoformat()},
            **extra,
        })
    events.append({
        "id": "demo-allday", "summary": "Reading week starts",
        "start": {"date": (today + timedelta(days=10)).isoformat()},
        "end": {"date": (today + timedelta(days=11)).isoformat()},
    })
    return events


def demo_snapshot(now: datetime):
    """Return (constraints, display rows) for a demo calendar around ``now``."""
    return import_google_events(demo_google_events(now.date()))
