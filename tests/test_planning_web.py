"""Exercise persisted inputs through real HTTP planner handlers and templates."""

from datetime import datetime, timedelta

from fastapi.testclient import TestClient
from web_helpers import authenticated_client
import pytest

from app import create_app, web
from app.schemas import CalendarEvent, EventCategory, SleepSession, UserProfile
from app.services import db
from app.time_utils import instant

NOW = datetime(2026, 11, 16, 14)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "planning.db")
    monkeypatch.setattr(web, "local_now", lambda: NOW)
    monkeypatch.setenv("SESSION_SECRET", "planning-tests-only-secret")
    with authenticated_client(create_app(load_environment=False)) as client:
        yield client


def _short_night():
    session = SleepSession(start=datetime(2026, 11, 16, 2), end=datetime(2026, 11, 16, 7), efficiency=0.9)
    db.upsert_sleep_session("watch-night", session, now=NOW)


def test_dashboard_reads_saved_night_and_preserves_sleep_gaps(client):
    empty = client.get("/api/me/dashboard").json()
    assert empty["has_sleep_history"] is False
    assert empty["snapshot"]["last_night"] is None
    assert "profile defaults" in client.get("/dashboard").text

    _short_night()
    saved = client.get("/api/me/dashboard").json()
    assert saved["has_sleep_history"] is True
    assert saved["snapshot"]["last_night"]["hours_asleep"] == 4.5
    assert saved["snapshot"]["last_night"]["hrv_ms"] is None
    assert saved["snapshot"]["now"] == "2026-11-16T14:00:00+00:00"
    assert any(point["alertness"] is None for point in saved["curve"])
    assert "4h 30m" in client.get("/dashboard").text


def test_saved_calendar_and_profile_constrain_bedtime_alarm(client):
    _short_night()
    db.save_profile(UserProfile(name="You", morning_routine_min=45))
    exam = CalendarEvent(title="Morning exam", start=datetime(2026, 11, 17, 8),
                         end=datetime(2026, 11, 17, 9), category=EventCategory.EXAM, travel_min=30)
    db.replace_calendar_events([exam])
    result = client.get("/api/me/bedtime-recommendation")
    assert result.status_code == 200
    plan = result.json()
    assert plan["feasible"] is True
    assert plan["first_event"] == "Morning exam"
    # The 20-minute grogginess minimum fits inside the 45-minute routine.
    assert instant(datetime.fromisoformat(plan["alarm"])) <= instant(exam.start) - timedelta(minutes=75)
    assert "Morning exam" in client.get("/sleep-planner").text


def test_nap_button_calls_brain_with_validated_inputs(client):
    _short_night()
    result = client.post("/api/me/nap-recommendation", json={"tiredness": "very_tired"})
    assert result.status_code == 200
    plan = result.json()
    assert isinstance(plan["should_nap"], bool)
    assert plan["lie_down"] == "2026-11-16T14:00:00+00:00"
    assert plan["headline"]
    if plan["should_nap"]:
        assert plan["sleep_min"] in (10, 20, 90)
        assert datetime.fromisoformat(plan["alarm"]) > datetime.fromisoformat(plan["lie_down"])
    response = client.post("/nap-planner", data={"tiredness": "very_tired"})
    assert response.status_code == 200
    if plan["should_nap"]:
        assert "Tap for a nap" in response.text and 'id="nap-result"' in response.text
        assert f"{plan['sleep_min']} min" in response.text
        assert web.clock_ceil(datetime.fromisoformat(plan["alarm"])) in response.text
    else:
        assert "Skip the nap for now." in response.text and plan["headline"] in response.text
    assert "doesn't record a sleep or set an alarm" in response.text
    assert client.get("/nap-planner").status_code == 200


@pytest.mark.parametrize("payload", [
    {"tiredness": 2}, {"tiredness": "exhausted"},
    {"tiredness": "tired", "now": "2000-01-01T00:00:00"},
    {"tiredness": "tired", "goal": "whatever"},
])
def test_nap_rejects_old_or_untrusted_request_shape(client, payload):
    assert client.post("/api/me/nap-recommendation", json=payload).status_code == 422


def test_what_if_is_hypothetical_and_does_not_save_sleep(client):
    _short_night()
    before = db.list_sleep_records()
    payload = {"bedtime": "2026-11-16T23:00:00+00:00", "alarm": "2026-11-17T07:00:00+00:00"}
    result = client.post("/api/me/what-if", json=payload)
    assert result.status_code == 200
    assert result.json()["sleep_h"] == 7.2
    assert result.json()["curve"]
    page = client.post("/sleep-planner/what-if", data=payload)
    assert page.status_code == 200
    assert "Your hypothetical next day" in page.text
    assert db.list_sleep_records() == before


@pytest.mark.parametrize("payload", [
    {"bedtime": "2026-11-16T12:00:00", "alarm": "2026-11-17T07:00:00"},
    {"bedtime": "2026-11-16T23:00:00", "alarm": "2026-11-16T22:00:00"},
    {"bedtime": "2026-11-16T23:00:00", "alarm": "2026-11-19T07:00:00"},
    {"bedtime": "2026-12-01T23:00:00", "alarm": "2026-12-02T07:00:00"},
    {"bedtime": "not a timestamp", "alarm": "2026-11-17T07:00:00"},
])
def test_invalid_what_if_returns_validation_response(client, payload):
    assert client.post("/api/me/what-if", json=payload).status_code == 422
    page = client.post("/sleep-planner/what-if", data=payload)
    assert page.status_code == 422
    assert 'role="alert"' in page.text


def test_zero_sleep_what_if_keeps_all_nighter_warning(client):
    result = client.post("/api/me/what-if", json={
        "bedtime": "2026-11-16T23:00:00", "alarm": "2026-11-16T23:00:00",
    })
    assert result.status_code == 200
    assert result.json()["sleep_h"] == 0
    assert any("all-nighter" in message for message in result.json()["warnings"])


def test_what_if_form_preserves_second_occurrence_of_repeated_clock(client, monkeypatch):
    monkeypatch.setattr(web, "local_now", lambda: datetime(2026, 10, 24, 14))
    payload = {"bedtime": "2026-10-25T01:30:00+00:00", "alarm": "2026-10-25T02:30:00+00:00"}
    response = client.post("/sleep-planner/what-if", data=payload)
    assert response.status_code == 200
    assert 'value="2026-10-25T01:30:00+00:00"' in response.text
    assert 'value="2026-10-25T02:30:00+00:00"' in response.text
    assert "estimated sleep 0.9 h" in response.text
    assert "2026-10-25T01:30:00+01:00" not in response.text
    api = client.post("/api/me/what-if", json=payload).json()
    assert api["bedtime"] == payload["bedtime"]
    assert api["sleep_h"] == 0.9


def test_nap_now_picks_a_time_and_explains_it_without_gemini(client, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    _short_night()
    lecture = CalendarEvent(title="Maths lecture", start=datetime(2026, 11, 16, 16),
                            end=datetime(2026, 11, 16, 17), category=EventCategory.LECTURE)
    db.replace_calendar_events([lecture])
    plan = client.post("/api/me/nap-now").json()
    assert plan["status"] in ("now", "later", "none")
    assert "with_nap" not in plan  # the button doesn't need the chart data
    if plan["status"] != "none":
        lie, alarm = datetime.fromisoformat(plan["lie_down"]), datetime.fromisoformat(plan["alarm"])
        assert instant(lie) >= instant(NOW) - timedelta(minutes=1) and instant(alarm) > instant(lie)
        assert plan["sleep_min"] in (10, 20, 90)
        assert instant(alarm) <= instant(lecture.start)  # never sleeps through the lecture
    reason = client.post("/api/me/nap-now/reason").json()
    assert reason["source"] == "doze" and reason["reason"]
    if plan["status"] != "none":
        assert "Maths lecture" in reason["reason"]


def test_nap_reason_uses_gemini_text_when_available(client, monkeypatch):
    from app.services import nap_now
    seen = {}

    def fake_gemini(facts):
        seen.update(facts)
        return "Your lecture is at 16:00, so a short nap now keeps you sharp for it."

    monkeypatch.setattr(nap_now, "_gemini_reason", fake_gemini)
    _short_night()
    reason = client.post("/api/me/nap-now/reason").json()
    assert reason == {"reason": "Your lecture is at 16:00, so a short nap now keeps you sharp for it.", "source": "gemini"}
    # Gemini only sees facts the maths produced, never raw records or account details.
    assert {"decision", "lie_down", "doze_reasons", "events_today_and_tomorrow_morning"} <= set(seen)
    assert "email" not in str(seen)


def test_today_shows_one_nap_button_not_a_no_nap_message(client):
    _short_night()
    page = client.get("/dashboard").text
    assert "data-nap-widget" in page and "Tap for a nap" in page
    assert "No nap is suggested right now" not in page and "Review nap options" not in page
