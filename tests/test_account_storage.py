"""Account persistence must never inherit another person's prototype data."""

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import sqlite3

import pytest

from app.schemas import CalendarEvent, SleepSession, UserProfile
from app.services import db

NOW = datetime(2026, 11, 16, 14)


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "accounts.db")
    with db.account_scope(None):
        db.init_db()
        yield db


@pytest.fixture
def accounts(storage):
    return (storage.create_account("alice@example.com", "fixture-password-hash-a", "Alice"),
            storage.create_account("bob@example.com", "fixture-password-hash-b", "Bob"))


def night(**changes):
    return SleepSession.model_validate({"start": "2026-11-15T23:00+00:00",
        "end": "2026-11-16T07:00+00:00", "efficiency": 0.9, **changes})


def event(title):
    return CalendarEvent(title=title, start="2026-11-17T09:00Z", end="2026-11-17T10:00Z")


def token_hash(label):
    return hashlib.sha256(label.encode()).hexdigest()


def test_account_identity_is_case_insensitive_and_public_records_exclude_hash(storage):
    account = storage.create_account("  ALICE@Example.com ", "private-fixture-hash", "  Alice  ")
    assert len(account["id"]) == 32
    assert account["email"] == "alice@example.com"
    assert account["name"] == "Alice"
    assert account["onboarding_complete"] is False
    assert "password_hash" not in account
    assert "password_hash" not in storage.get_account(account["id"])
    assert storage.find_account_by_email(" ALICE@EXAMPLE.COM ")["password_hash"] == "private-fixture-hash"
    with pytest.raises(ValueError, match="already exists"):
        storage.create_account("alice@example.com", "different-hash", "Another Alice")
    assert storage.find_account_by_email("unknown@example.com") is None


def test_old_unassigned_records_are_preserved_but_never_adopted(storage):
    storage.save_profile(UserProfile(name="Legacy owner"))
    storage.upsert_sleep_session("legacy", night(), now=NOW)
    storage.replace_calendar_events([event("Legacy calendar")], metadata={"source": "legacy"})
    storage.save_google_connection("legacy-ref", '{"token":"legacy-token"}')
    with storage.get_connection() as conn:
        conn.execute("INSERT INTO sleep_history (date, duration, quality) VALUES ('2026-11-15',7,'good')")
    account = storage.create_account("new@example.com", "fixture-hash", "New person")
    storage.init_db()  # An additive repeat migration cannot move or erase rows.
    with storage.account_scope(account["id"]):
        loaded = storage.load_user_data()
        assert loaded.profile.name == "New person"
        assert loaded.sessions == [] and loaded.events == []
        assert storage.list_sleep_records() == []
        assert storage.fetch_sleep_history() == []
        assert storage.load_calendar_metadata() == {}
        assert storage.load_google_connection("legacy-ref") is None
        assert storage.latest_google_connection_reference() is None
    assert storage.load_user_data().profile.name == "Legacy owner"
    assert len(storage.list_sleep_records()) == 1
    assert storage.fetch_sleep_history()[0]["quality"] == "good"
    assert storage.load_calendar_metadata() == {"source": "legacy"}
    assert storage.load_google_connection("legacy-ref") is not None
    assert storage.latest_google_connection_reference() is None


def test_profiles_and_same_source_ids_are_owned_independently(storage, accounts):
    alice, bob = accounts
    with storage.account_scope(alice["id"]):
        storage.save_profile(UserProfile(name="Alice updated", usual_wake="08:00"))
        storage.upsert_sleep_session("watch-night", night(rating=4), now=NOW)
    with storage.account_scope(bob["id"]):
        storage.upsert_sleep_session("watch-night", night(rating=1), now=NOW)
        storage.upsert_sleep_session("bob-only", night(start="2026-11-13T23:00Z", end="2026-11-14T07:00Z"), now=NOW)
        assert storage.load_user_data().profile.name == "Bob"
        assert storage.load_user_data().sessions[-1].rating == 1
    with storage.account_scope(alice["id"]):
        assert storage.load_user_data().profile.usual_wake.hour == 8
        assert storage.list_sleep_records()[0]["rating"] == 4
        assert storage.delete_sleep_session("bob-only") is False
        assert storage.delete_sleep_session("watch-night") is True
    with storage.account_scope(bob["id"]):
        assert len(storage.list_sleep_records()) == 2
    assert storage.get_account(alice["id"])["name"] == "Alice updated"
    assert storage.list_sleep_records() == []


def test_account_overlap_rejection_is_atomic_and_does_not_check_other_users(storage, accounts):
    alice, bob = accounts
    with storage.account_scope(bob["id"]):
        storage.upsert_sleep_session("shared-time", night(), now=NOW)
    with storage.account_scope(alice["id"]):
        storage.upsert_sleep_session("first", night(), now=NOW)
        storage.upsert_sleep_session("second", night(start="2026-11-14T23:00Z", end="2026-11-15T07:00Z"), now=NOW)
        with pytest.raises(ValueError, match="overlap"):
            storage.upsert_sleep_session("second", night(start="2026-11-16T06:00Z", end="2026-11-16T08:00Z"), now=NOW)
        second = next(item for item in storage.list_sleep_records() if item["source_id"] == "second")
        assert second["start"].startswith("2026-11-14")
    with storage.account_scope(bob["id"]):
        assert len(storage.list_sleep_records()) == 1


def test_calendar_and_provider_refs_cannot_cross_account_boundary(storage, accounts):
    alice, bob = accounts
    with storage.account_scope(alice["id"]):
        storage.replace_calendar_events([event("Alice event")], metadata={"source": "alice"})
        storage.save_google_connection("same-ref", '{"token":"alice-token"}')
        storage.save_oauth_pending("same-pending", {"state": "alice-state"})
    with storage.account_scope(bob["id"]):
        assert storage.load_google_connection("same-ref") is None
        assert storage.pop_oauth_pending("same-pending") is None
        assert storage.latest_google_connection_reference() is None
        storage.replace_calendar_events([event("Bob event")], metadata={"source": "bob"})
        storage.save_google_connection("same-ref", '{"token":"bob-token"}')
        storage.save_oauth_pending("same-pending", {"state": "bob-state"})
    with storage.account_scope(alice["id"]):
        assert storage.load_calendar_events()[0].title == "Alice event"
        assert storage.latest_google_connection_reference() == "same-ref"
        assert "alice-token" in storage.load_google_connection("same-ref")
        assert storage.pop_oauth_pending("same-pending") == {"state": "alice-state"}
        assert storage.pop_oauth_pending("same-pending") is None
        storage.delete_google_connection("same-ref")
        storage.replace_calendar_events([], metadata={})
    with storage.account_scope(bob["id"]):
        assert storage.load_calendar_events()[0].title == "Bob event"
        assert storage.load_calendar_metadata() == {"source": "bob"}
        assert "bob-token" in storage.load_google_connection("same-ref")
        assert storage.pop_oauth_pending("same-pending") == {"state": "bob-state"}
    assert storage.load_calendar_events() == []


def test_server_sessions_are_hashed_expiring_and_revocable(storage, accounts):
    alice, bob = accounts
    future = datetime.now(timezone.utc) + timedelta(days=1)
    storage.create_account_session(alice["id"], token_hash("alice-session"), future.isoformat())
    storage.create_account_session(bob["id"], token_hash("bob-session"), future)
    assert storage.resolve_account_session(token_hash("alice-session"))["id"] == alice["id"]
    assert "password_hash" not in storage.resolve_account_session(token_hash("alice-session"))
    assert storage.resolve_account_session("raw-session-token") is None
    with pytest.raises(ValueError, match="SHA-256"):
        storage.create_account_session(alice["id"], "raw-session-token", future)
    storage.delete_account_session(token_hash("alice-session"))
    assert storage.resolve_account_session(token_hash("alice-session")) is None
    assert storage.resolve_account_session(token_hash("bob-session"))["id"] == bob["id"]
    storage.create_account_session(alice["id"], token_hash("expired"), datetime.now(timezone.utc) - timedelta(seconds=1))
    assert storage.resolve_account_session(token_hash("expired")) is None
    with storage.get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM account_sessions WHERE token_hash = ?", (token_hash("expired"),)).fetchone()[0] == 0
    with pytest.raises(ValueError, match="offset-bearing"):
        storage.create_account_session(alice["id"], token_hash("naive"), datetime(2026,11,17))


def test_scope_is_restored_and_unknown_accounts_never_fall_back(storage, accounts):
    alice, _ = accounts
    assert storage.current_account_id() is None
    with storage.account_scope(alice["id"]):
        assert storage.current_account_id() == alice["id"]
        with pytest.raises(ValueError, match="Unknown account"):
            with storage.account_scope("missing"):
                pass
        with pytest.raises(ValueError, match="Unknown account"):
            with storage.account_scope(""):
                pass
        assert storage.current_account_id() == alice["id"]
        with storage.account_scope(None):
            assert storage.current_account_id() is None
        assert storage.current_account_id() == alice["id"]
    assert storage.current_account_id() is None


def test_context_isolated_between_concurrent_async_tasks_and_worker_threads(storage, accounts):
    async def worker(account, label):
        with storage.account_scope(account["id"]):
            await asyncio.sleep(0)
            await asyncio.to_thread(storage.save_profile, UserProfile(name=label))
            await asyncio.sleep(0)
            loaded = await asyncio.to_thread(storage.load_user_data)
            return storage.current_account_id(), loaded.profile.name
    async def run():
        return await asyncio.gather(worker(accounts[0], "Alice concurrent"), worker(accounts[1], "Bob concurrent"))
    assert asyncio.run(run()) == [(accounts[0]["id"], "Alice concurrent"), (accounts[1]["id"], "Bob concurrent")]
    assert storage.current_account_id() is None


def test_onboarding_is_owned_revisioned_and_confirmation_is_atomic(storage, accounts):
    alice, bob = accounts
    with storage.account_scope(alice["id"]):
        initial = storage.load_onboarding(alice["id"])
        assert initial == {"messages": [], "draft": {}, "_revision": 0}
        first = storage.save_onboarding(alice["id"], {"messages": [{"role":"user","content":"I sleep late"}], "draft":{"usual_bedtime":"23:30"}}, expected_revision=0)
        assert first["_revision"] == 1
        with pytest.raises(ValueError, match="changed"):
            storage.save_onboarding(alice["id"], {"draft": {}}, expected_revision=0)
        assert storage.load_onboarding(alice["id"])["draft"] == {"usual_bedtime":"23:30"}
        with pytest.raises(ValueError, match="does not own"):
            storage.load_onboarding(bob["id"])
        with pytest.raises(ValueError, match="changed"):
            storage.complete_onboarding(alice["id"], UserProfile(name="Confirmed"), expected_revision=0)
        assert storage.get_account(alice["id"])["onboarding_complete"] is False
        storage.complete_onboarding(alice["id"], UserProfile(name="Confirmed Alice", usual_wake="08:30"), "More afternoon focus", expected_revision=1)
        assert storage.get_account(alice["id"])["name"] == "Confirmed Alice"
        assert storage.get_account(alice["id"])["onboarding_complete"] is True
        assert storage.load_user_data().profile.usual_wake.minute == 30
        assert storage.load_user_data().sessions == []
        with storage.get_connection() as conn:
            goal = conn.execute("SELECT payload FROM scoped_settings WHERE user_id = ? AND key = 'sleep_goal'", (alice["id"],)).fetchone()[0]
        assert goal == '"More afternoon focus"'
        with pytest.raises(ValueError, match="already set up"):
            storage.save_onboarding(alice["id"], {"draft": {}})
    with storage.account_scope(bob["id"]):
        assert storage.load_onboarding(bob["id"])["draft"] == {}
        assert storage.get_account(bob["id"])["onboarding_complete"] is False
        assert storage.load_user_data().profile.name == "Bob"


def test_failed_confirmation_rolls_back_profile_account_name_and_completion(storage, accounts):
    alice, _ = accounts
    with storage.account_scope(alice["id"]):
        storage.save_profile(UserProfile(name="Original name"))
        storage.save_onboarding(alice["id"], {"draft": {"name":"Blocked name"}})
        with storage.get_connection() as conn:
            conn.execute("CREATE TRIGGER fail_name BEFORE UPDATE OF name ON accounts WHEN NEW.name = 'Blocked name' BEGIN SELECT RAISE(ABORT, 'fixture blocked name'); END")
        with pytest.raises(sqlite3.IntegrityError, match="fixture blocked"):
            storage.complete_onboarding(alice["id"], UserProfile(name="Blocked name"))
        assert storage.load_user_data().profile.name == "Original name"
        account = storage.get_account(alice["id"])
        assert account["name"] == "Original name" and account["onboarding_complete"] is False
