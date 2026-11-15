"""Account-owned persistence for the shared brain input models.

SQLite payloads preserve timezone offsets. The old single-user tables remain
untouched and unassigned; web requests bind an authenticated account with
``account_scope`` before using this adapter. A missing scope is reserved for
legacy service tooling and is never an authenticated user's fallback.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import sqlite3
from uuid import uuid4

from app.schemas import CalendarEvent, SleepSession, UserData, UserProfile, normalise_sessions
from app.services.sleep_history import primary_nights
from app.time_utils import instant

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = PROJECT_ROOT / "instance" / "doze.db"
DB_PATH = DEFAULT_DB_PATH
SOURCE_ID_PATTERN = re.compile(r"[A-Za-z0-9_.:-]{1,200}")
TOKEN_HASH_PATTERN = re.compile(r"[a-f0-9]{64}")
_ACCOUNT_ID: ContextVar[str | None] = ContextVar(
    "doze_account_id", default=None)


def current_account_id() -> str | None:
    return _ACCOUNT_ID.get()


@contextmanager
def account_scope(user_id: str | None):
    """Bind one account for this async/thread context and restore it on exit."""
    if user_id is not None:
        if not isinstance(user_id, str) or not user_id or get_account(user_id) is None:
            raise ValueError("Unknown account.")
    token = _ACCOUNT_ID.set(user_id)
    try:
        yield
    finally:
        _ACCOUNT_ID.reset(token)


@contextmanager
def get_connection():
    """Commit successful writes, roll back failures, and always close handles."""
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init_db():
    path = Path(DB_PATH)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    legacy = PROJECT_ROOT / "doze.db"
    if path == DEFAULT_DB_PATH and not path.exists() and legacy.exists():
        # Keep the team's original database untouched; migrate a local copy.
        shutil.copy2(legacy, path)
    if path == DEFAULT_DB_PATH:
        path.parent.chmod(0o700)
        if path.exists():
            path.chmod(0o600)
    with get_connection() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS sleep_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT NOT NULL,
                duration REAL NOT NULL, quality TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS profiles (
                id INTEGER PRIMARY KEY CHECK (id = 1), payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sleep_sessions (
                source_id TEXT PRIMARY KEY, payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS calendar_events (
                position INTEGER PRIMARY KEY, payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY, payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS google_connections (
                reference TEXT PRIMARY KEY, credentials_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS oauth_pending (
                reference TEXT PRIMARY KEY, payload TEXT NOT NULL
            );
            
            CREATE TABLE IF NOT EXISTS dreams (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
                           
            CREATE TABLE IF NOT EXISTS accounts (
                id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL, name TEXT NOT NULL,
                onboarding_complete INTEGER NOT NULL DEFAULT 0 CHECK (onboarding_complete IN (0, 1)),
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS account_sessions (
                token_hash TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                expires_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS account_sessions_owner ON account_sessions(user_id);
            CREATE TABLE IF NOT EXISTS account_onboarding (
                user_id TEXT PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE,
                payload TEXT NOT NULL, revision INTEGER NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS scoped_profiles (
                user_id TEXT PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE,
                payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS scoped_sleep_sessions (
                user_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                source_id TEXT NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY (user_id, source_id)
            );
            CREATE TABLE IF NOT EXISTS scoped_calendar_events (
                user_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                position INTEGER NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY (user_id, position)
            );
            CREATE TABLE IF NOT EXISTS scoped_settings (
                user_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                key TEXT NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY (user_id, key)
            );
            CREATE TABLE IF NOT EXISTS scoped_google_connections (
                user_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                reference TEXT NOT NULL, credentials_json TEXT NOT NULL,
                PRIMARY KEY (user_id, reference)
            );
            CREATE TABLE IF NOT EXISTS scoped_oauth_pending (
                user_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                reference TEXT NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY (user_id, reference)
            );
        """)
    if path == DEFAULT_DB_PATH:
        path.chmod(0o600)


def _safe_account(row) -> dict | None:
    if row is None:
        return None
    return {"id": row["id"], "email": row["email"], "name": row["name"],
            "onboarding_complete": bool(row["onboarding_complete"]), "created_at": row["created_at"]}


def _require_account(conn, user_id: str, *, matching_scope: bool = False):
    if matching_scope and current_account_id() not in (None, user_id):
        raise ValueError("This account does not own that setup conversation.")
    row = conn.execute("SELECT * FROM accounts WHERE id = ?",
                       (user_id,)).fetchone()
    if row is None:
        raise ValueError("Unknown account.")
    return row


def create_account(email: str, password_hash: str, name: str) -> dict:
    email, name = email.strip().casefold(), name.strip()
    if not email or len(email) > 254:
        raise ValueError("Enter a valid email address.")
    if not name or len(name) > 200:
        raise ValueError("Enter a name of at most 200 characters.")
    if not password_hash or len(password_hash) > 1000:
        raise ValueError("A password hash is required.")
    user_id = uuid4().hex
    with get_connection() as conn:
        try:
            conn.execute("INSERT INTO accounts (id, email, password_hash, name, created_at) VALUES (?, ?, ?, ?, ?)",
                         (user_id, email, password_hash, name, datetime.now(timezone.utc).isoformat()))
        except sqlite3.IntegrityError:
            raise ValueError(
                "An account already exists for that email address.") from None
        return _safe_account(_require_account(conn, user_id))


def find_account_by_email(email: str) -> dict | None:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM accounts WHERE email = ?",
                           (email.strip().casefold(),)).fetchone()
    if row is None:
        return None
    return {**_safe_account(row), "password_hash": row["password_hash"]}


def get_account(user_id: str) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM accounts WHERE id = ?", (user_id,)).fetchone()
    return _safe_account(row)


def _session_hash(value: str):
    if not isinstance(value, str) or TOKEN_HASH_PATTERN.fullmatch(value) is None:
        raise ValueError("Store only a SHA-256 session token hash.")


def _utc(value: str | datetime) -> datetime:
    parsed = datetime.fromisoformat(value.replace(
        "Z", "+00:00")) if isinstance(value, str) else value
    if parsed.tzinfo is None:
        raise ValueError("Use an offset-bearing session expiry.")
    return parsed.astimezone(timezone.utc)


def create_account_session(user_id: str, token_hash: str, expires_at: str | datetime):
    _session_hash(token_hash)
    expiry = _utc(expires_at).isoformat()
    with get_connection() as conn:
        _require_account(conn, user_id)
        conn.execute("INSERT INTO account_sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
                     (token_hash, user_id, expiry))


def resolve_account_session(token_hash: str) -> dict | None:
    if not isinstance(token_hash, str) or TOKEN_HASH_PATTERN.fullmatch(token_hash) is None:
        return None
    with get_connection() as conn:
        row = conn.execute("SELECT accounts.*, account_sessions.expires_at FROM account_sessions "
                           "JOIN accounts ON accounts.id = account_sessions.user_id WHERE token_hash = ?",
                           (token_hash,)).fetchone()
        if row is None:
            return None
        if _utc(row["expires_at"]) <= datetime.now(timezone.utc):
            conn.execute(
                "DELETE FROM account_sessions WHERE token_hash = ?", (token_hash,))
            return None
        return _safe_account(row)


def delete_account_session(token_hash: str):
    with get_connection() as conn:
        conn.execute(
            "DELETE FROM account_sessions WHERE token_hash = ?", (token_hash,))


def _profile(conn) -> UserProfile:
    user_id = current_account_id()
    if user_id is None:
        row = conn.execute(
            "SELECT payload FROM profiles WHERE id = 1").fetchone()
        return UserProfile.model_validate_json(row["payload"]) if row else UserProfile(name="You")
    account = _require_account(conn, user_id)
    row = conn.execute(
        "SELECT payload FROM scoped_profiles WHERE user_id = ?", (user_id,)).fetchone()
    return UserProfile.model_validate_json(row["payload"]) if row else UserProfile(name=account["name"])


def _sleep_rows(conn):
    user_id = current_account_id()
    if user_id is None:
        return conn.execute("SELECT source_id, payload FROM sleep_sessions ORDER BY source_id").fetchall()
    return conn.execute("SELECT source_id, payload FROM scoped_sleep_sessions WHERE user_id = ? ORDER BY source_id",
                        (user_id,)).fetchall()


def _calendar_rows(conn):
    user_id = current_account_id()
    if user_id is None:
        return conn.execute("SELECT payload FROM calendar_events ORDER BY position").fetchall()
    return conn.execute("SELECT payload FROM scoped_calendar_events WHERE user_id = ? ORDER BY position", (user_id,)).fetchall()


def load_user_data() -> UserData:
    with get_connection() as conn:
        # Profile, history and calendar come from one snapshot.
        conn.execute("BEGIN")
        return UserData(profile=_profile(conn),
                        sessions=[SleepSession.model_validate_json(
                            row["payload"]) for row in _sleep_rows(conn)],
                        events=[CalendarEvent.model_validate_json(row["payload"]) for row in _calendar_rows(conn)])


def _write_profile(conn, profile: UserProfile, user_id: str | None):
    if user_id is None:
        conn.execute("INSERT INTO profiles (id, payload) VALUES (1, ?) "
                     "ON CONFLICT(id) DO UPDATE SET payload = excluded.payload", (profile.model_dump_json(),))
    else:
        _require_account(conn, user_id)
        conn.execute("INSERT INTO scoped_profiles (user_id, payload) VALUES (?, ?) "
                     "ON CONFLICT(user_id) DO UPDATE SET payload = excluded.payload", (user_id, profile.model_dump_json()))
        if profile.name.strip():
            conn.execute("UPDATE accounts SET name = ? WHERE id = ?",
                         (profile.name.strip(), user_id))


def save_profile(profile: UserProfile) -> UserProfile:
    profile = UserProfile.model_validate(profile.model_dump())
    with get_connection() as conn:
        _write_profile(conn, profile, current_account_id())
    return profile


def list_sleep_records() -> list[dict]:
    with get_connection() as conn:
        records = [(row["source_id"], SleepSession.model_validate_json(
            row["payload"])) for row in _sleep_rows(conn)]
    records.sort(key=lambda row: instant(row[1].start), reverse=True)
    return [{"source_id": source_id, **session.model_dump(mode="json"),
             "hours_asleep": round(session.hours_asleep, 3)} for source_id, session in records]


def upsert_sleep_session(source_id: str, session: SleepSession, *, now: datetime | None = None) -> dict:
    if source_id in (".", "..") or SOURCE_ID_PATTERN.fullmatch(source_id) is None:
        raise ValueError(
            "source_id must contain 1-200 ASCII letters, digits, underscores, hyphens, dots or colons, and cannot be '.' or '..'")
    session = SleepSession.model_validate(session.model_dump())
    if session.latency_min is not None and (
        session.latency_min > session.minutes + 1e-6
        or session.latency_min + session.hours_asleep * 60 > session.minutes + 1e-6
    ):
        raise ValueError(
            "minutes to fall asleep plus time asleep cannot exceed time in bed; reconcile efficiency, latency and recorded times")
    now = now or datetime.now(timezone.utc)
    if instant(session.end) > instant(now):
        raise ValueError(
            "only completed sleep can be saved; wake-up time must not be in the future")
    user_id = current_account_id()
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        previous = [SleepSession.model_validate_json(row["payload"]) for row in _sleep_rows(conn)
                    if row["source_id"] != source_id]
        combined = normalise_sessions([*previous, session])
        if len(combined) != len(previous) + 1:
            raise ValueError(
                "this sleep interval already exists; update its original source_id")
        primary_nights(combined)
        if user_id is None:
            conn.execute("INSERT INTO sleep_sessions (source_id, payload) VALUES (?, ?) "
                         "ON CONFLICT(source_id) DO UPDATE SET payload = excluded.payload", (source_id, session.model_dump_json()))
        else:
            conn.execute("INSERT INTO scoped_sleep_sessions (user_id, source_id, payload) VALUES (?, ?, ?) "
                         "ON CONFLICT(user_id, source_id) DO UPDATE SET payload = excluded.payload", (user_id, source_id, session.model_dump_json()))
    return {"source_id": source_id, **session.model_dump(mode="json"), "hours_asleep": round(session.hours_asleep, 3)}


def delete_sleep_session(source_id: str) -> bool:
    user_id = current_account_id()
    with get_connection() as conn:
        if user_id is None:
            return conn.execute("DELETE FROM sleep_sessions WHERE source_id = ?", (source_id,)).rowcount > 0
        return conn.execute("DELETE FROM scoped_sleep_sessions WHERE user_id = ? AND source_id = ?", (user_id, source_id)).rowcount > 0


def fetch_sleep_history() -> list[dict]:
    """Unassigned legacy rows are never exposed to a newly registered account."""
    if current_account_id() is not None:
        return []
    with get_connection() as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM sleep_history ORDER BY date DESC")]


def _save_setting(conn, key: str, payload: str, user_id: str | None):
    if user_id is None:
        conn.execute(
            "INSERT INTO settings (key, payload) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET payload = excluded.payload", (key, payload))
    else:
        conn.execute("INSERT INTO scoped_settings (user_id, key, payload) VALUES (?, ?, ?) "
                     "ON CONFLICT(user_id, key) DO UPDATE SET payload = excluded.payload", (user_id, key, payload))


def replace_calendar_events(events: list[CalendarEvent], metadata: dict | None = None):
    validated = [CalendarEvent.model_validate(
        event.model_dump()) for event in events]
    metadata_json = json.dumps(
        metadata, allow_nan=False) if metadata is not None else None
    user_id = current_account_id()
    with get_connection() as conn:
        if user_id is None:
            conn.execute("DELETE FROM calendar_events")
            conn.executemany("INSERT INTO calendar_events (position, payload) VALUES (?, ?)", [
                             (i, event.model_dump_json()) for i, event in enumerate(validated)])
        else:
            conn.execute(
                "DELETE FROM scoped_calendar_events WHERE user_id = ?", (user_id,))
            conn.executemany("INSERT INTO scoped_calendar_events (user_id, position, payload) VALUES (?, ?, ?)", [
                             (user_id, i, event.model_dump_json()) for i, event in enumerate(validated)])
        if metadata_json is not None:
            _save_setting(conn, "calendar_metadata", metadata_json, user_id)


def load_calendar_events() -> list[CalendarEvent]:
    with get_connection() as conn:
        return [CalendarEvent.model_validate_json(row["payload"]) for row in _calendar_rows(conn)]


def save_calendar_metadata(metadata: dict):
    payload = json.dumps(metadata, allow_nan=False)
    with get_connection() as conn:
        _save_setting(conn, "calendar_metadata", payload, current_account_id())


def load_calendar_metadata() -> dict:
    user_id = current_account_id()
    with get_connection() as conn:
        if user_id is None:
            row = conn.execute(
                "SELECT payload FROM settings WHERE key = 'calendar_metadata'").fetchone()
        else:
            row = conn.execute(
                "SELECT payload FROM scoped_settings WHERE user_id = ? AND key = 'calendar_metadata'", (user_id,)).fetchone()
    return json.loads(row["payload"]) if row else {}


def save_google_connection(reference: str, credentials_json: str):
    user_id = current_account_id()
    with get_connection() as conn:
        if user_id is None:
            conn.execute("INSERT INTO google_connections (reference, credentials_json) VALUES (?, ?) "
                         "ON CONFLICT(reference) DO UPDATE SET credentials_json = excluded.credentials_json", (reference, credentials_json))
        else:
            conn.execute("INSERT INTO scoped_google_connections (user_id, reference, credentials_json) VALUES (?, ?, ?) "
                         "ON CONFLICT(user_id, reference) DO UPDATE SET credentials_json = excluded.credentials_json", (user_id, reference, credentials_json))


def load_google_connection(reference: str) -> str | None:
    user_id = current_account_id()
    with get_connection() as conn:
        if user_id is None:
            row = conn.execute(
                "SELECT credentials_json FROM google_connections WHERE reference = ?", (reference,)).fetchone()
        else:
            row = conn.execute(
                "SELECT credentials_json FROM scoped_google_connections WHERE user_id = ? AND reference = ?", (user_id, reference)).fetchone()
    return row["credentials_json"] if row else None


def latest_google_connection_reference() -> str | None:
    """Restore only this account's connection after a new application login."""
    user_id = current_account_id()
    if user_id is None:
        return None
    with get_connection() as conn:
        row = conn.execute("SELECT reference FROM scoped_google_connections WHERE user_id = ? ORDER BY rowid DESC LIMIT 1",
                           (user_id,)).fetchone()
    return row["reference"] if row else None


def delete_google_connection(reference: str):
    user_id = current_account_id()
    with get_connection() as conn:
        if user_id is None:
            conn.execute(
                "DELETE FROM google_connections WHERE reference = ?", (reference,))
        else:
            conn.execute(
                "DELETE FROM scoped_google_connections WHERE user_id = ? AND reference = ?", (user_id, reference))


def save_oauth_pending(reference: str, payload: dict):
    user_id = current_account_id()
    encoded = json.dumps(payload, allow_nan=False)
    with get_connection() as conn:
        if user_id is None:
            conn.execute(
                "INSERT INTO oauth_pending (reference, payload) VALUES (?, ?) ON CONFLICT(reference) DO UPDATE SET payload = excluded.payload", (reference, encoded))
        else:
            conn.execute("INSERT INTO scoped_oauth_pending (user_id, reference, payload) VALUES (?, ?, ?) "
                         "ON CONFLICT(user_id, reference) DO UPDATE SET payload = excluded.payload", (user_id, reference, encoded))


def pop_oauth_pending(reference: str) -> dict | None:
    user_id = current_account_id()
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if user_id is None:
            row = conn.execute(
                "SELECT payload FROM oauth_pending WHERE reference = ?", (reference,)).fetchone()
            conn.execute(
                "DELETE FROM oauth_pending WHERE reference = ?", (reference,))
        else:
            row = conn.execute(
                "SELECT payload FROM scoped_oauth_pending WHERE user_id = ? AND reference = ?", (user_id, reference)).fetchone()
            conn.execute(
                "DELETE FROM scoped_oauth_pending WHERE user_id = ? AND reference = ?", (user_id, reference))
    return json.loads(row["payload"]) if row else None


def load_onboarding(user_id: str) -> dict:
    with get_connection() as conn:
        _require_account(conn, user_id, matching_scope=True)
        row = conn.execute(
            "SELECT payload, revision FROM account_onboarding WHERE user_id = ?", (user_id,)).fetchone()
    return {**json.loads(row["payload"]), "_revision": row["revision"]} if row else {"messages": [], "draft": {}, "_revision": 0}


def save_onboarding(user_id: str, state: dict, *, expected_revision: int | None = None) -> dict:
    payload = {key: value for key, value in state.items() if key !=
               "_revision"}
    encoded = json.dumps(payload, allow_nan=False)
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        account = _require_account(conn, user_id, matching_scope=True)
        if account["onboarding_complete"]:
            raise ValueError("Your profile is already set up.")
        row = conn.execute(
            "SELECT revision FROM account_onboarding WHERE user_id = ?", (user_id,)).fetchone()
        revision = row["revision"] if row else 0
        if expected_revision is not None and expected_revision != revision:
            raise ValueError(
                "Your setup conversation changed. Reload it and try again.")
        revision += 1
        conn.execute("INSERT INTO account_onboarding (user_id, payload, revision, updated_at) VALUES (?, ?, ?, ?) "
                     "ON CONFLICT(user_id) DO UPDATE SET payload = excluded.payload, revision = excluded.revision, updated_at = excluded.updated_at",
                     (user_id, encoded, revision, datetime.now(timezone.utc).isoformat()))
    return {**payload, "_revision": revision}


def complete_onboarding(user_id: str, profile: UserProfile, goal: str | None = None, *, expected_revision: int | None = None):
    profile = UserProfile.model_validate(profile.model_dump())
    if not profile.name.strip():
        raise ValueError("Enter your name before saving your profile.")
    if goal is not None and (not isinstance(goal, str) or len(goal) > 2000):
        raise ValueError("Keep your sleep goal within 2000 characters.")
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        account = _require_account(conn, user_id, matching_scope=True)
        if account["onboarding_complete"]:
            raise ValueError("Your profile is already set up.")
        row = conn.execute(
            "SELECT revision FROM account_onboarding WHERE user_id = ?", (user_id,)).fetchone()
        if expected_revision is not None and expected_revision != (row["revision"] if row else 0):
            raise ValueError(
                "Your setup conversation changed. Reload it and try again.")
        _write_profile(conn, profile, user_id)
        if goal is not None:
            _save_setting(conn, "sleep_goal",
                          json.dumps(goal.strip()), user_id)
        conn.execute(
            "UPDATE accounts SET onboarding_complete = 1 WHERE id = ?", (user_id,))


def create_dream_table():
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS dreams (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                image_path TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)


def save_dream(user_id, title, content):
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO dreams (user_id, title, content)
            VALUES (?, ?, ?)
            """,
            (user_id, title, content)
        )
        return cursor.lastrowid


def get_dreams(user_id):
    with get_connection() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT * FROM dreams
            WHERE user_id = ?
            ORDER BY created_at DESC, id DESC
            """,
            (user_id,)
        ).fetchall()

    return [dict(row) for row in rows]


def get_dream(user_id, dream_id):
    with get_connection() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM dreams WHERE id = ? AND user_id = ?",
            (dream_id, user_id)
        ).fetchone()
    return dict(row) if row else None


def save_dream_image(user_id, dream_id, image_path):
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE dreams SET image_path = ?
            WHERE id = ? AND user_id = ?
            """,
            (image_path, dream_id, user_id)
        )
