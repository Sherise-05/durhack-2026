
import os
import asyncpg
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")


async def fetch_calendar_events():
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        rows = await conn.fetch("""
            SELECT *
            FROM calendar_events
            ORDER BY start_time ASC
        """)
        return [dict(row) for row in rows]
    finally:
        await conn.close()


async def fetch_sleep_history():
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        rows = await conn.fetch("""
            SELECT *
            FROM sleep_records
            ORDER BY sleep_start DESC
        """)
        return [dict(row) for row in rows]
    finally:
        await conn.close()
