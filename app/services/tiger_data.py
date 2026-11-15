
from app.services.db import get_connection


async def fetch_calendar_events():
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT *
            FROM calendar_events
            ORDER BY start_time ASC
        """).fetchall()

    return [dict(row) for row in rows]


async def fetch_sleep_history():
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT *
            FROM sleep_records
            ORDER BY sleep_start DESC
        """).fetchall()

    return [dict(row) for row in rows]
