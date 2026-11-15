"""Validate a Google snapshot before using it as sleep-planning constraints."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.schemas import CalendarEvent, EventCategory
from app.time_utils import instant, local_datetime


class CalendarImportError(ValueError):
    pass


def _timestamp(value: dict) -> datetime:
    stamp = datetime.fromisoformat(value["dateTime"].replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        zone = value.get("timeZone")
        if not zone:
            raise ValueError("timed events need an offset or named timezone")
        # Validate nonexistent wall clocks before attaching their source zone.
        local_datetime(stamp, zone)
        source_zone = ZoneInfo(zone)
        if stamp.replace(tzinfo=source_zone, fold=0).utcoffset() != stamp.replace(tzinfo=source_zone, fold=1).utcoffset():
            raise ValueError("ambiguous source clock needs an explicit offset")
        stamp = stamp.replace(tzinfo=source_zone)
    return stamp


def import_google_events(raw_events: list[dict]) -> tuple[list[CalendarEvent], list[dict]]:
    """Return busy timed constraints and a separately labelled display list.

    All-day entries do not identify an alarm deadline; free/transparent entries
    do not reserve time. Both remain visible without constraining sleep. Bad
    timed records reject the whole sync, retaining the prior saved snapshot.
    """
    by_id: dict[str, dict] = {}
    for index, raw in enumerate(raw_events):
        by_id[str(raw.get("id", f"row-{index}"))] = raw

    constraints: list[CalendarEvent] = []
    display: list[dict] = []
    try:
        for source_id, raw in by_id.items():
            if raw.get("status") == "cancelled":
                continue
            title = raw.get("summary") or "Untitled event"
            start, end = raw["start"], raw["end"]
            if "date" in start and "date" in end:
                first, last = date.fromisoformat(start["date"]), date.fromisoformat(end["date"])
                if last <= first:
                    raise ValueError("all-day event must end after it starts")
                display.append({
                    "id": source_id, "title": title, "start": first.isoformat(),
                    "end": last.isoformat(), "all_day": True, "blocks_sleep": False,
                })
                continue
            event = CalendarEvent(
                title=title, start=_timestamp(start), end=_timestamp(end),
                category=EventCategory.OTHER, location=raw.get("location"),
            )
            declined = any(
                attendee.get("self") and attendee.get("responseStatus") == "declined"
                for attendee in raw.get("attendees", [])
            )
            busy = raw.get("transparency") != "transparent" and not declined
            if busy:
                constraints.append(event)
            encoded = event.model_dump(mode="json")
            display.append({
                "id": source_id, "title": event.title, "start": encoded["start"],
                "end": encoded["end"], "all_day": False, "blocks_sleep": busy,
            })
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise CalendarImportError(
            "Some calendar entries have invalid or missing times. The previous "
            "saved calendar was kept; check those entries before syncing again."
        ) from exc
    constraints.sort(key=lambda event: instant(event.start))
    display.sort(key=lambda item: instant(datetime.fromisoformat(item["start"])))
    return constraints, display
