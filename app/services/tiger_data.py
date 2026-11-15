
from app.services.db import execute_query


async def fetch_calendar_events():
    rows = await execute_query("""
        SELECT *
        FROM calendar_events
        ORDER BY start_time ASC
    """)
    return [dict(row) for row in rows]


async def fetch_sleep_history():
    rows = await execute_query("""
        SELECT *
        FROM sleep_records
        ORDER BY sleep_start DESC
    """)
    return [dict(row) for row in rows]
