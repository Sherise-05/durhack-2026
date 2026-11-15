"""The Today page must reflect evidence and contextual actions, not mock data."""

from datetime import datetime
import json
import re

from fastapi.testclient import TestClient
from web_helpers import authenticated_client
import pytest

from app import create_app, web
from app.schemas import CalendarEvent, EventCategory, SleepSession
from app.services import db


NOW = datetime(2026, 11, 16, 14)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "dashboard-test-secret")
    monkeypatch.setenv("SESSION_HTTPS_ONLY", "false")
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "dashboard.db")
    monkeypatch.setattr(web, "local_now", lambda: NOW)
    with authenticated_client(create_app(load_environment=False)) as client:
        yield client


def record(source, start, end, *, is_nap=False):
    db.upsert_sleep_session(source, SleepSession(start=start, end=end, is_nap=is_nap), now=NOW)


def payload(response, element_id):
    match = re.search(r'<script type="application/json" id="' + element_id + r'">(.*?)</script>', response.text, re.S)
    assert match is not None
    return json.loads(match.group(1))


def test_new_user_and_nap_only_history_do_not_fabricate_primary_sleep(client):
    empty = client.get("/dashboard")
    assert empty.status_code == 200
    # Logging is offered by the morning check-in card; its pre-filled routine is not saved.
    assert empty.context["last_night_guess"] is not None
    assert 'id="check-in"' in empty.text
    assert "Starting estimate from your profile" in empty.text
    assert 'id="dashboard-sleep"' not in empty.text
    assert 'id="dashboard-curve"' not in empty.text

    record("nap-only", "2026-11-16T12:00Z", "2026-11-16T12:20Z", is_nap=True)
    nap_only = client.get("/dashboard")
    assert nap_only.context["has_sleep_history"] is True
    assert nap_only.context["has_completed_history"] is False
    assert nap_only.context["snapshot"].last_night is None
    assert 'id="check-in"' in nap_only.text  # a nap is not last night's sleep
    assert 'id="dashboard-sleep"' not in nap_only.text


def test_history_chart_keeps_missing_dates_unknown_and_preserves_sleep_gaps(client):
    record("saturday", "2026-11-13T23:00Z", "2026-11-14T07:00Z")
    record("monday", "2026-11-16T02:00Z", "2026-11-16T07:00Z")
    response = client.get("/dashboard")
    assert response.status_code == 200
    assert "4h 30m" in response.text
    chart = payload(response, "dashboard-sleep")
    assert chart["points"] == [
        {"label": "10 Nov", "value": None}, {"label": "11 Nov", "value": None},
        {"label": "12 Nov", "value": None}, {"label": "13 Nov", "value": None},
        {"label": "14 Nov", "value": 7.2}, {"label": "15 Nov", "value": None},
        {"label": "16 Nov", "value": 4.5},
    ]
    assert chart["reference"] == response.context["snapshot"].sleep_need_h
    assert any(point["alertness"] is None for point in payload(response, "dashboard-curve")["Forecast"])


def test_next_commitment_skips_completed_and_rest_events(client):
    db.replace_calendar_events([
        CalendarEvent(title="Old meeting", start="2026-11-16T12:00Z", end="2026-11-16T13:00Z"),
        CalendarEvent(title="Rest break", start="2026-11-16T14:30Z", end="2026-11-16T15:00Z", category=EventCategory.REST),
        CalendarEvent(title="Study group", start="2026-11-16T16:00Z", end="2026-11-16T17:00Z", category=EventCategory.STUDY),
        CalendarEvent(title="Tomorrow exam", start="2026-11-17T09:00Z", end="2026-11-17T10:00Z", category=EventCategory.EXAM),
    ])
    response = client.get("/dashboard")
    assert response.context["next_event"].title == "Study group"
    assert response.context["next_event_in_progress"] is False


def test_evening_action_reviews_bedtime_instead_of_automatically_starting_a_nap(client, monkeypatch):
    record("monday", "2026-11-16T02:00Z", "2026-11-16T07:00Z")
    monkeypatch.setattr(web, "local_now", lambda: datetime(2026, 11, 16, 21))
    response = client.get("/dashboard")
    assert response.context["next_action"]["kind"] == "bedtime"
    assert response.context["next_action"]["href"] == "/sleep-planner"
    assert response.context["next_action"]["nap"] is None
    assert len(db.list_sleep_records()) == 1
