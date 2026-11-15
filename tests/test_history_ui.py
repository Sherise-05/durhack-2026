"""History and profile flows preserve real inputs through the shared interface."""

from datetime import date, datetime
from html.parser import HTMLParser

from fastapi.testclient import TestClient
from web_helpers import authenticated_client
import pytest

from app import create_app, web
from app.schemas import CalendarEvent, Mode, SleepSession, UserProfile
from app.services import db


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "history-ui.db")
    monkeypatch.setattr(web, "local_now", lambda: datetime(2026, 11, 16, 14))
    monkeypatch.setenv("SESSION_SECRET", "history-ui-test-only")
    monkeypatch.setenv("SESSION_HTTPS_ONLY", "false")
    with authenticated_client(create_app(load_environment=False)) as client:
        yield client


def profile_form(**updates):
    return {"name": "Maya", "age_band": "18-25", "chronotype": "late",
            "usual_bedtime": "23:45", "usual_wake": "08:15",
            "sleep_latency_min": "18", "morning_routine_min": "40", **updates}


def test_profile_has_its_own_save_flow_and_preserves_unedited_settings(client):
    db.save_profile(UserProfile(name="Before", mode=Mode.HOLIDAY, term_starts=date(2026, 12, 1)))
    assert client.get("/profile").status_code == 200
    result = client.post("/profile", data=profile_form(), follow_redirects=False)
    assert result.status_code == 303
    assert result.headers["location"] == "/profile?saved=1"
    saved = client.get(result.headers["location"])
    assert saved.status_code == 200
    assert "Your profile is saved" in saved.text
    profile = db.load_user_data().profile
    assert profile.name == "Maya"
    assert profile.usual_wake.hour == 8
    assert profile.usual_wake.minute == 15
    assert profile.mode == Mode.HOLIDAY
    assert profile.term_starts == date(2026, 12, 1)


def test_invalid_profile_stays_editable_without_overwriting_saved_values(client):
    db.save_profile(UserProfile(name="Before"))
    result = client.post("/profile", data=profile_form(name="   ", morning_routine_min="65"))
    assert result.status_code == 422
    assert 'role="alert"' in result.text
    assert 'action="/profile"' in result.text
    assert 'value="65"' in result.text
    assert "name must not be empty" in result.text
    assert db.load_user_data().profile.name == "Before"


def test_profile_explains_automatic_mode_without_changing_selected_mode(client):
    db.replace_calendar_events([CalendarEvent(title="Morning exam", category="exam",
        start="2026-11-17T09:00Z", end="2026-11-17T10:00Z")])
    result = client.get("/profile")
    assert result.status_code == 200
    assert "Exam mode" in result.text
    assert "Morning exam is tomorrow" in result.text
    assert "Applied automatically" in result.text
    assert db.load_user_data().profile.mode == Mode.NORMAL


def test_history_edit_keeps_exact_offsets_for_repeated_hour(client):
    session = SleepSession(start="2026-10-25T01:15+01:00", end="2026-10-25T01:45+00:00", efficiency=1)
    assert client.put("/api/me/sleep-sessions/dst-night", json=session.model_dump(mode="json")).status_code == 200
    page = client.get("/sleep-history?edit=dst-night")
    assert page.status_code == 200

    class Inputs(HTMLParser):
        def __init__(self):
            super().__init__()
            self.fields = {}

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == "input" and "name" in attrs:
                self.fields[attrs["name"]] = attrs

    parser = Inputs()
    parser.feed(page.text)
    assert parser.fields["start"]["value"] == "2026-10-25T01:15:00+01:00"
    assert parser.fields["end"]["value"] == "2026-10-25T01:45:00+00:00"
    assert "data-datetime-field" in parser.fields["start"]
    assert "data-datetime-field" in parser.fields["end"]
    assert 'action="/profile"' not in page.text
