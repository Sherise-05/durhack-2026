"""Calendar integration checks use fake Google responses, never real accounts."""

import base64
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from app.pages import calendar as routes
from app.schemas import CalendarEvent
from app.services import db
from app.services.calendar_ingest import CalendarImportError, import_google_events
from app.services.google_auth import CalendarFetchResult


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "doze.db")
    db.init_db()
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="calendar-integration-test")
    app.mount("/static", StaticFiles(directory=Path(__file__).resolve().parents[1] / "app" / "static"), name="static")
    app.include_router(routes.router)
    with TestClient(app) as client:
        yield client


def raw_event(event_id="one", *, start="2026-11-16T09:00:00Z", end="2026-11-16T10:00:00Z", **extra):
    return {"id": event_id, "summary": event_id, "start": {"dateTime": start}, "end": {"dateTime": end}, **extra}


def cookie_payload(client):
    return json.loads(base64.b64decode(client.cookies.get("session").split(".")[0]))


def fake_google(monkeypatch, raw_events=None):
    calls = {"flows": [], "exchanges": []}

    class Flow:
        redirect_uri = "http://testserver/auth/callback"
        credentials = SimpleNamespace(to_json=lambda: json.dumps({"token": "private-google-token"}))

        def authorization_url(self, **kwargs):
            return "https://accounts.google.com/fake", "test-state"

        def fetch_token(self, **kwargs):
            calls["exchanges"].append(kwargs)

    def create_flow(**kwargs):
        calls["flows"].append(kwargs)
        return Flow()

    monkeypatch.setattr(routes, "create_flow", create_flow)
    monkeypatch.setattr(routes, "fetch_calendar_events", lambda data: CalendarFetchResult(raw_events or [], json.dumps(data)))
    return calls


def connect(client):
    assert client.get("/auth/google", follow_redirects=False).status_code == 303
    return client.get("/auth/callback?state=test-state&code=fake-code", follow_redirects=False)


def test_all_routes_remain_registered_and_missing_config_is_useful(client, monkeypatch):
    for key in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REDIRECT_URI"):
        monkeypatch.delenv(key, raising=False)
    response = client.get("/auth/google", follow_redirects=False)
    assert response.status_code == 503
    assert "GOOGLE_CLIENT_ID" in response.json()["detail"]
    assert client.get("/auth/callback").status_code == 400
    page = client.get("/calendar")
    assert page.status_code == 200
    assert "No upcoming events." in page.text
    assert "University Lecture" not in page.text


def test_import_preserves_real_instants_and_separates_all_day_free_cancelled():
    raw = [
        raw_event(start="2026-10-25T01:15:00+01:00", end="2026-10-25T01:45:00+00:00"),
        raw_event("free", transparency="transparent"),
        raw_event("cancelled", status="cancelled"),
        raw_event("declined", attendees=[{"self": True, "responseStatus": "declined"}]),
        {"id": "all-day", "summary": "Holiday", "start": {"date": "2026-11-16"}, "end": {"date": "2026-11-17"}},
    ]
    events, display = import_google_events(raw)
    assert len(events) == 1
    assert events[0].minutes == 90
    assert events[0].model_dump(mode="json")["start"].endswith("+01:00")
    assert events[0].model_dump(mode="json")["end"].endswith("+00:00")
    assert events[0].category.value == "other"
    assert len(display) == 4
    assert next(item for item in display if item["id"] == "all-day")["blocks_sleep"] is False
    assert next(item for item in display if item["id"] == "free")["blocks_sleep"] is False


def test_import_respects_source_timezone_and_rejects_unzoned_times():
    raw = raw_event(start="2026-11-16T09:00:00", end="2026-11-16T10:00:00")
    with pytest.raises(CalendarImportError):
        import_google_events([raw])
    raw["start"]["timeZone"] = raw["end"]["timeZone"] = "America/New_York"
    events, _ = import_google_events([raw])
    assert events[0].start.hour == 14
    raw["start"] = {"dateTime": "2026-10-25T01:30:00", "timeZone": "Europe/London"}
    with pytest.raises(CalendarImportError):
        import_google_events([raw])
    raw["start"] = {"dateTime": "2026-03-29T01:30:00", "timeZone": "Europe/London"}
    with pytest.raises(CalendarImportError):
        import_google_events([raw])


def test_callback_consumes_state_and_keeps_tokens_out_of_cookie(client, monkeypatch):
    calls = fake_google(monkeypatch, [raw_event()])
    response = connect(client)
    assert response.status_code == 303
    assert response.headers["location"] == "/calendar"
    payload = cookie_payload(client)
    assert set(payload) == {"google_connection_ref"}
    assert "private-google-token" not in json.dumps(payload)
    assert "private-google-token" in db.load_google_connection(payload["google_connection_ref"])
    assert calls["exchanges"] == [{"code": "fake-code", "timeout": 15}]
    assert calls["flows"][0]["code_verifier"] == calls["flows"][1]["code_verifier"]
    assert len(db.load_user_data().events) == 1
    assert client.get("/auth/callback?state=test-state&code=fake-code").status_code == 400
    assert len(calls["exchanges"]) == 1


def test_wrong_state_and_expired_pending_login_do_not_exchange_token(client, monkeypatch):
    calls = fake_google(monkeypatch)
    client.get("/auth/google", follow_redirects=False)
    assert client.get("/auth/callback?state=wrong&code=fake-code").status_code == 400
    client.get("/auth/google", follow_redirects=False)
    reference = cookie_payload(client)["oauth_pending_ref"]
    pending = db.pop_oauth_pending(reference)
    pending["created_at"] = datetime.now(timezone.utc).timestamp() - 601
    db.save_oauth_pending(reference, pending)
    assert client.get("/auth/callback?state=test-state&code=fake-code").status_code == 400
    assert calls["exchanges"] == []


def test_sync_replaces_deleted_events_and_disconnect_clears_saved_calendar(client, monkeypatch):
    fake_google(monkeypatch, [raw_event("before")])
    connect(client)
    monkeypatch.setattr(routes, "fetch_calendar_events", lambda data: CalendarFetchResult([raw_event("after")], json.dumps(data)))
    assert client.post("/calendar/sync", follow_redirects=False).status_code == 303
    assert [event.title for event in db.load_user_data().events] == ["after"]
    reference = cookie_payload(client)["google_connection_ref"]
    assert client.post("/calendar/disconnect", follow_redirects=False).status_code == 303
    assert db.load_google_connection(reference) is None
    assert db.load_user_data().events == []
    assert db.load_calendar_metadata() == {}


def test_provider_or_import_failure_keeps_last_snapshot_without_leaking_secrets(client, monkeypatch):
    fake_google(monkeypatch, [raw_event("saved")])
    connect(client)

    def fail(_):
        raise ValueError("private-provider-token-and-database-password")

    monkeypatch.setattr(routes, "fetch_calendar_events", fail)
    response = client.post("/calendar/sync")
    assert response.status_code == 502
    assert "private-provider" not in response.text
    assert [event.title for event in db.load_user_data().events] == ["saved"]
    assert "private-provider" not in json.dumps(db.load_calendar_metadata())
    monkeypatch.setattr(routes, "fetch_calendar_events", lambda data: CalendarFetchResult([{"id": "bad"}], json.dumps(data)))
    assert client.post("/calendar/sync").status_code == 502
    assert [event.title for event in db.load_user_data().events] == ["saved"]
    assert "previous saved calendar was kept" in client.get("/calendar").text


def test_calendar_get_never_sends_external_requests(client, monkeypatch):
    fake_google(monkeypatch)
    connect(client)

    def unexpected(_):
        raise AssertionError("GET must display saved data without external calls")

    monkeypatch.setattr(routes, "fetch_calendar_events", unexpected)
    assert client.get("/calendar").status_code == 200


def test_google_fetch_pages_entire_snapshot_and_returns_refreshed_credentials(monkeypatch):
    import google.oauth2.credentials
    import googleapiclient.discovery
    import google_auth_httplib2
    import httplib2
    from app.services.google_auth import fetch_calendar_events

    queries, refreshes = [], []
    credentials = SimpleNamespace(
        expired=True, refresh_token="fake-refresh",
        refresh=lambda request: refreshes.append(request),
        to_json=lambda: '{"token":"fresh-fake-token"}',
    )
    monkeypatch.setattr(google.oauth2.credentials.Credentials, "from_authorized_user_info", lambda *args, **kwargs: credentials)
    monkeypatch.setattr(httplib2, "Http", lambda **kwargs: kwargs)
    monkeypatch.setattr(google_auth_httplib2, "AuthorizedHttp", lambda *args, **kwargs: kwargs)

    class Events:
        def list(self, **kwargs):
            queries.append(kwargs)
            if kwargs["pageToken"] is None:
                return SimpleNamespace(execute=lambda: {"items": [raw_event("first")], "nextPageToken": "second-page"})
            return SimpleNamespace(execute=lambda: {"items": [raw_event("second")]})

    monkeypatch.setattr(googleapiclient.discovery, "build", lambda *args, **kwargs: SimpleNamespace(events=lambda: Events()))
    result = fetch_calendar_events({"token": "fake"}, now=datetime(2026, 11, 15, tzinfo=timezone.utc))
    assert [event["id"] for event in result.events] == ["first", "second"]
    assert result.credentials_json == '{"token":"fresh-fake-token"}'
    assert len(refreshes) == 1
    assert queries[1]["pageToken"] == "second-page"
    assert queries[0]["timeMin"] == "2026-11-08T00:00:00+00:00"
    assert queries[0]["timeMax"] == "2026-12-15T00:00:00+00:00"


@pytest.mark.parametrize("callback_host, expected_origin", [
    ("localhost", "http://localhost:8000"),
    ("[::1]", "http://[::1]:8000"),
])
def test_loopback_login_uses_registered_host_before_creating_cookie(client, monkeypatch, callback_host, expected_origin):
    calls = fake_google(monkeypatch)
    original_flow = routes.create_flow

    def configured_flow(**kwargs):
        flow = original_flow(**kwargs)
        flow.redirect_uri = f"http://{callback_host}:8000/auth/callback"
        return flow

    monkeypatch.setattr(routes, "create_flow", configured_flow)
    response = client.get("http://127.0.0.1:8000/auth/google", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == expected_origin + "/auth/google"
    assert "set-cookie" not in response.headers
    with db.get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM oauth_pending").fetchone()[0] == 0
    canonical = client.get(response.headers["location"], follow_redirects=False)
    assert canonical.headers["location"] == "https://accounts.google.com/fake"
    callback = client.get(expected_origin + "/auth/callback?state=test-state&code=fake-code", follow_redirects=False)
    assert callback.status_code == 303
    assert callback.headers["location"] == "/calendar"
    assert len(calls["exchanges"]) == 1


@pytest.mark.parametrize("callback_uri", [
    "http://localhost:9000/auth/callback",
    "http://localhost:0/auth/callback",
    "https://localhost:8000/auth/callback",
    "http://elsewhere.example:8000/auth/callback",
    "http://localhost:8000/not-the-callback",
    "http://user:password@localhost:8000/auth/callback",
])
def test_login_never_redirects_to_external_or_incompatible_callback_origins(client, monkeypatch, callback_uri):
    fake_google(monkeypatch)
    original_flow = routes.create_flow

    def configured_flow(**kwargs):
        flow = original_flow(**kwargs)
        flow.redirect_uri = callback_uri
        return flow

    monkeypatch.setattr(routes, "create_flow", configured_flow)
    response = client.get("http://127.0.0.1:8000/auth/google", follow_redirects=False)
    assert response.status_code == 503
    assert "location" not in response.headers
    assert "set-cookie" not in response.headers
    with db.get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM oauth_pending").fetchone()[0] == 0


def test_loopback_login_treats_implicit_and_explicit_default_ports_equally(client, monkeypatch):
    fake_google(monkeypatch)
    original_flow = routes.create_flow

    def configured_flow(**kwargs):
        flow = original_flow(**kwargs)
        flow.redirect_uri = "http://localhost:80/auth/callback"
        return flow

    monkeypatch.setattr(routes, "create_flow", configured_flow)
    response = client.get("http://127.0.0.1/auth/google", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "http://localhost:80/auth/google"
    assert "set-cookie" not in response.headers
