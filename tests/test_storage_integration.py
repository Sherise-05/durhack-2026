"""Persistence must feed real, validated observations into the brain."""

from datetime import datetime

from fastapi.testclient import TestClient
from web_helpers import authenticated_client
import pytest

from app import create_app, web
from app.schemas import CalendarEvent, SleepSession, UserProfile
from app.services import db
from app.time_utils import hours_between, instant

NOW = datetime(2026, 11, 16, 14)


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "local.db")
    db.init_db()
    return db


@pytest.fixture
def client(storage, monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "test-secret-only")
    monkeypatch.setattr(web, "local_now", lambda: NOW)
    with authenticated_client(create_app(load_environment=False)) as client:
        yield client


def session(**changes):
    return SleepSession.model_validate({
        "start": "2026-11-15T23:00+00:00", "end": "2026-11-16T07:00+00:00",
        "efficiency": 0.9, **changes,
    })


def test_profile_and_full_sleep_schema_round_trip(storage):
    profile = UserProfile(name="Test", usual_wake="08:15", chronotype="late")
    storage.save_profile(profile)
    original = session(episode_id="night-1", rating=4, hrv_ms=54.2, deep_pct=0.2,
                       rem_pct=0.25, awakenings=2, resting_hr=57, latency_min=12)
    storage.upsert_sleep_session("watch-record-1", original, now=NOW)
    loaded = storage.load_user_data()
    assert loaded.profile == profile
    assert loaded.sessions == [original]
    assert storage.list_sleep_records()[0]["source_id"] == "watch-record-1"


def test_repeated_hour_offsets_survive_storage(storage):
    original = session(start="2026-10-25T01:15+01:00", end="2026-10-25T01:45+00:00", efficiency=1)
    storage.upsert_sleep_session("dst-night", original, now=NOW)
    restored = storage.load_user_data().sessions[0]
    assert instant(restored.start) == instant(original.start)
    assert instant(restored.end) == instant(original.end)
    assert hours_between(restored.start, restored.end) == 1.5
    stored = storage.list_sleep_records()[0]
    assert stored["start"].endswith("+01:00")
    assert stored["end"].endswith("+00:00")


def test_source_update_and_overlap_rejection_are_atomic(storage):
    storage.upsert_sleep_session("one", session(), now=NOW)
    storage.upsert_sleep_session("two", session(start="2026-11-14T23:00Z", end="2026-11-15T07:00Z"), now=NOW)
    with pytest.raises(ValueError, match="overlap"):
        storage.upsert_sleep_session("two", session(start="2026-11-16T06:00Z", end="2026-11-16T08:00Z"), now=NOW)
    records = storage.list_sleep_records()
    assert len(records) == 2
    assert next(record for record in records if record["source_id"] == "two")["start"].startswith("2026-11-14")
    with pytest.raises(ValueError, match="already exists"):
        storage.upsert_sleep_session("duplicate", session(), now=NOW)
    assert len(storage.load_user_data().sessions) == 2


def test_reused_episode_id_cannot_break_future_calculations(storage):
    storage.upsert_sleep_session("one", session(episode_id="same-night"), now=NOW)
    with pytest.raises(ValueError, match="at most 24 hours"):
        storage.upsert_sleep_session("two", session(start="2026-11-13T23:00Z", end="2026-11-14T07:00Z", episode_id="same-night"), now=NOW)
    assert len(storage.load_user_data().sessions) == 1


@pytest.mark.parametrize("efficiency,latency", [(0.9, 48), (1, 0), (0, 480), (0.9, 48.0000005)])
def test_latency_and_asleep_time_can_fill_but_not_exceed_time_in_bed(storage, efficiency, latency):
    storage.upsert_sleep_session("one", session(efficiency=efficiency, latency_min=latency), now=NOW)
    assert storage.load_user_data().sessions[0].latency_min == latency


@pytest.mark.parametrize("efficiency,latency", [(0.9, 49), (0, 481), (1, 1)])
def test_impossible_latency_is_rejected_before_storage(storage, efficiency, latency):
    with pytest.raises(ValueError, match="reconcile efficiency, latency and recorded times"):
        storage.upsert_sleep_session("one", session(efficiency=efficiency, latency_min=latency), now=NOW)
    assert storage.list_sleep_records() == []


def test_api_rejects_impossible_latency_and_preserves_previous_record(client):
    assert client.put("/api/me/sleep-sessions/one", json=session(latency_min=48).model_dump(mode="json")).status_code == 200
    rejected = client.put("/api/me/sleep-sessions/one", json=session(latency_min=49).model_dump(mode="json"))
    assert rejected.status_code == 422
    assert "reconcile efficiency, latency and recorded times" in rejected.json()["detail"]
    assert client.get("/api/me/sleep-history").json()["records"][0]["latency_min"] == 48


@pytest.mark.parametrize("source_id", ["", ".", "..", "watch/night", "watch\\night", "watch night", "café", "watch%2Fnight", "watch?night", "watch#night", "a\n", "a" * 201])
def test_unaddressable_source_ids_are_rejected_without_writing(storage, source_id):
    with pytest.raises(ValueError, match="source_id must contain 1-200 ASCII"):
        storage.upsert_sleep_session(source_id, session(), now=NOW)
    assert storage.list_sleep_records() == []


def test_safe_watch_source_id_remains_addressable_and_invalid_form_id_is_422(client):
    source_id = "watch:night-1.v2_test"
    saved = client.put(f"/api/me/sleep-sessions/{source_id}", json=session().model_dump(mode="json"))
    assert saved.status_code == 200
    assert saved.json()["source_id"] == source_id
    assert client.patch(f"/api/me/sleep-sessions/{source_id}/rating", json={"rating": 4}).status_code == 200
    assert client.delete(f"/api/me/sleep-sessions/{source_id}").status_code == 200
    invalid = client.post("/sleep-history", data={"source_id": "../inaccessible", "start": "2026-11-15T23:00Z",
                                                "end": "2026-11-16T07:00Z", "efficiency": "0.9"})
    assert invalid.status_code == 422
    assert "source_id must contain 1-200 ASCII" in invalid.text
    assert client.get("/api/me/sleep-history").json()["records"] == []


def test_record_and_check_in_updates_recompute_learning_without_duplicates(client):
    created = client.put("/api/me/sleep-sessions/one", json=session().model_dump(mode="json"))
    assert created.status_code == 200
    assert client.get("/api/me/sleep-history").json()["personal"]["need"]["n"] == 0
    assert client.patch("/api/me/sleep-sessions/one/rating", json={"rating": 1}).status_code == 200
    poor = client.get("/api/me/sleep-history").json()
    assert poor["personal"]["need"]["n"] == 1
    assert client.patch("/api/me/sleep-sessions/one/rating", json={"rating": 5}).status_code == 200
    refreshed = client.get("/api/me/sleep-history").json()
    assert len(refreshed["records"]) == 1
    assert refreshed["personal"]["need"]["n"] == 1
    assert refreshed["personal"]["need"]["mean"] < poor["personal"]["need"]["mean"]
    assert client.delete("/api/me/sleep-sessions/one").status_code == 200
    assert client.get("/api/me/sleep-history").json()["records"] == []
    assert client.delete("/api/me/sleep-sessions/one").status_code == 404


def test_invalid_history_is_a_validation_response(client):
    assert client.put("/api/me/sleep-sessions/future", json=session(
        start="2026-11-16T15:00Z", end="2026-11-16T15:30Z").model_dump(mode="json")).status_code == 422
    assert client.put("/api/me/sleep-sessions/invalid", json={
        "start": "2026-11-16T07:00Z", "end": "2026-11-16T06:00Z"}).status_code == 422
    assert client.put("/api/me/sleep-sessions/one", json=session().model_dump(mode="json")).status_code == 200
    overlap = client.put("/api/me/sleep-sessions/two", json=session(
        start="2026-11-16T06:00Z", end="2026-11-16T08:00Z").model_dump(mode="json"))
    assert overlap.status_code == 422
    assert len(client.get("/api/me/sleep-history").json()["records"]) == 1
    assert client.patch("/api/me/profile", json={"usual_wake": "25:00"}).status_code == 422
    assert client.patch("/api/me/profile", json={"unknown": True}).status_code == 422


def test_forms_render_save_and_edit_preserves_other_watch_fields(client):
    assert client.get("/sleep-history").status_code == 200
    client.put("/api/me/sleep-sessions/one", json=session(deep_pct=0.2, resting_hr=52).model_dump(mode="json"))
    form = {"source_id": "one", "start": "2026-11-15T23:00+00:00", "end": "2026-11-16T07:00+00:00",
            "efficiency": "0.9", "rating": "4", "episode_id": "", "hrv_ms": "", "latency_min": ""}
    assert client.post("/sleep-history", data=form, follow_redirects=False).status_code == 303
    updated = client.get("/api/me/sleep-history").json()["records"][0]
    assert updated["deep_pct"] == 0.2
    assert updated["resting_hr"] == 52
    assert updated["rating"] == 4
    assert client.get("/sleep-history?edit=one").status_code == 200
    invalid = client.post("/sleep-history", data={**form, "end": "2026-11-15T22:00Z"})
    assert invalid.status_code == 422
    assert "end must be after start" in invalid.text
    assert client.post("/sleep-history/one/delete", follow_redirects=False).status_code == 303


def test_unassigned_legacy_history_is_preserved_but_never_shared_with_new_accounts(client, storage):
    with storage.get_connection() as conn:
        conn.execute("INSERT INTO sleep_history (date, duration, quality) VALUES (?, ?, ?)",
                     ("2026-11-15", 7.5, "good"))
    payload = client.get("/api/me/sleep-history").json()
    assert payload["legacy_records"] == []
    assert payload["records"] == []
    assert payload["personal"]["need"]["n"] == 0
    assert "Older records awaiting exact times" not in client.get("/sleep-history").text
    with storage.account_scope(None):
        assert len(storage.fetch_sleep_history()) == 1
    assert storage.load_user_data().sessions == []


def test_calendar_snapshot_and_oauth_state_persist_atomically(storage):
    event = CalendarEvent(title="Lecture", start="2026-11-17T09:00Z", end="2026-11-17T10:00Z")
    storage.replace_calendar_events([event], metadata={"synced_at": "previous"})
    assert storage.load_user_data().events == [event]
    assert storage.load_calendar_metadata() == {"synced_at": "previous"}
    storage.save_google_connection("reference", '{"token": "test-only"}')
    assert storage.load_google_connection("reference") == '{"token": "test-only"}'
    storage.delete_google_connection("reference")
    assert storage.load_google_connection("reference") is None
    storage.save_oauth_pending("pending", {"state": "test-state"})
    assert storage.pop_oauth_pending("pending") == {"state": "test-state"}
    assert storage.pop_oauth_pending("pending") is None
    storage.replace_calendar_events([], metadata={})
    assert storage.load_user_data().events == []
    assert storage.load_calendar_metadata() == {}


def test_default_runtime_database_is_private_and_original_legacy_file_is_untouched(tmp_path, monkeypatch):
    import sqlite3
    legacy = tmp_path / "doze.db"
    with sqlite3.connect(legacy) as conn:
        conn.execute("CREATE TABLE sleep_history (id INTEGER PRIMARY KEY, date TEXT, duration REAL, quality TEXT)")
        conn.execute("INSERT INTO sleep_history VALUES (1, '2026-11-15', 7.5, 'good')")
    original = legacy.read_bytes()
    target = tmp_path / "instance" / "doze.db"
    monkeypatch.setattr(db, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(db, "DEFAULT_DB_PATH", target)
    monkeypatch.setattr(db, "DB_PATH", target)
    db.init_db()
    assert target.stat().st_mode & 0o777 == 0o600
    assert target.parent.stat().st_mode & 0o777 == 0o700
    assert db.fetch_sleep_history()[0]["duration"] == 7.5
    assert legacy.read_bytes() == original


def test_real_trip_creation_uses_server_date_and_edit_cannot_backdate_it(client, monkeypatch):
    trip = {"home_tz": "Europe/London", "destination_tz": "Asia/Tokyo",
            "departure": "2026-11-20T10:00+00:00", "arrival": "2026-11-21T09:00+09:00",
            "planned_from": "2026-01-01"}
    created = client.patch("/api/me/profile", json={"travel": trip})
    assert created.status_code == 200
    assert created.json()["travel"]["planned_from"] == "2026-11-16"
    monkeypatch.setattr(web, "local_now", lambda: datetime(2026, 11, 17, 14))
    edited = client.patch("/api/me/profile", json={"travel": {**trip, "planned_from": "2026-11-01", "travel_min": 90}})
    assert edited.status_code == 200
    assert edited.json()["travel"]["planned_from"] == "2026-11-16"
    assert edited.json()["travel"]["travel_min"] == 90
    assert client.patch("/api/me/profile", json={"travel": None}).status_code == 200
    assert client.patch("/api/me/profile", json={"travel": trip}).json()["travel"]["planned_from"] == "2026-11-17"
