import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[2] / "doze.db"


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sleep_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                duration REAL NOT NULL,
                quality TEXT NOT NULL
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
