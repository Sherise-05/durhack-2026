"""Elapsed-time arithmetic, separate from the clock times used for planning.

Legacy naive values use the demo's explicit Europe/London zone. Aware values
retain their instants; local clock values preserve fold through DST changes.
"""

from datetime import datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo

LOCAL_ZONE = "Europe/London"


def _zone(zone: str | tzinfo) -> tzinfo:
    return ZoneInfo(zone) if isinstance(zone, str) else zone


def instant(value: datetime, zone: str | tzinfo = LOCAL_ZONE) -> datetime:
    """An aware UTC instant; naive inputs belong to the specified local zone."""
    aware = value if value.tzinfo is not None else value.replace(tzinfo=_zone(zone))
    return aware.astimezone(timezone.utc)


def local_datetime(value: datetime, zone: str | tzinfo = LOCAL_ZONE) -> datetime:
    """Normalise input to a local naive clock while retaining its DST fold."""
    local = instant(value, zone).astimezone(_zone(zone)).replace(tzinfo=None)
    if value.tzinfo is None and local != value:
        raise ValueError("local time does not exist during the daylight-saving change; supply a valid timestamp")
    return local


def hours_between(a: datetime, b: datetime, zone: str | tzinfo = LOCAL_ZONE) -> float:
    return (instant(b, zone) - instant(a, zone)).total_seconds() / 3600


def add_elapsed(value: datetime, duration: timedelta, zone: str | tzinfo = LOCAL_ZONE) -> datetime:
    """Add actual elapsed time, preserving aware/naive representation and fold."""
    target = instant(value, zone) + duration
    if value.tzinfo is not None:
        return target.astimezone(value.tzinfo)
    return target.astimezone(_zone(zone)).replace(tzinfo=None)


def local_isoformat(value: datetime, zone: str | tzinfo = LOCAL_ZONE) -> str:
    """Keep offsets in JSON so repeated local hours survive a round trip."""
    return instant(value, zone).astimezone(_zone(zone)).isoformat()
