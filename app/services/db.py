
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[2] / "doze.db"


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_connection() as conn:

        # Existing sleep history table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sleep_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                duration REAL NOT NULL,
                quality TEXT NOT NULL
            )
        """)

        # User sleep preferences
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sleep_preferences (
                persona_id TEXT PRIMARY KEY,
                chronotype TEXT NOT NULL DEFAULT 'late',
                sleep_goal REAL NOT NULL DEFAULT 8,
                sleep_challenge TEXT NOT NULL DEFAULT 'late',
                schedule_flexibility TEXT NOT NULL DEFAULT 'some'
            )
        """)

        # Applied AI recommendations
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sleep_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                persona_id TEXT NOT NULL,
                action_type TEXT NOT NULL,
                action_value TEXT NOT NULL,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)


def fetch_sleep_history():
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT *
            FROM sleep_history
            ORDER BY date DESC
        """).fetchall()

    return [dict(row) for row in rows]
