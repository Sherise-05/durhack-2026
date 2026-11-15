"""Keyword categories and the fake demo calendar (no Google involved)."""

from datetime import datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from app.pages import calendar as routes
from app.schemas import EventCategory, Mode, UserData, UserProfile
from app.services import db
from app.services.calendar_ingest import categorise
from app.services.demo_calendar import demo_snapshot
from app.services.modes import effective_mode, exam_wake_time
from app.time_utils import local_datetime

NOW = local_datetime(datetime(2026, 11, 15, 10, 0))


@pytest.mark.parametrize("title, category", [
    ("Linear Algebra exam", EventCategory.EXAM),
    ("Driving test", EventCategory.EXAM),
    ("Driving lesson", EventCategory.TRAVEL),
    ("Maths lecture", EventCategory.LECTURE),
    ("Revision: probability", EventCategory.STUDY),
    ("Gym session", EventCategory.EXERCISE),
    ("Part-time work shift", EventCategory.WORK),
    ("Lunch with friends", EventCategory.SOCIAL),
    ("Dentist", EventCategory.OTHER),
    ("Contest prep", EventCategory.OTHER),  # whole words only: "test" inside "contest"
])
def test_categorise_from_title(title, category):
    assert categorise(title) == category


def test_demo_events_are_upcoming_and_switch_on_exam_mode():
    events, display = demo_snapshot(NOW)
    assert all(e.end > NOW for e in events)
    assert {e.category for e in events} >= {EventCategory.EXAM, EventCategory.LECTURE, EventCategory.TRAVEL}
    assert any(row["all_day"] for row in display)
    assert any(not row["blocks_sleep"] and not row["all_day"] for row in display)  # transparent social
    data = UserData(profile=UserProfile(name="Demo"), events=events)
    mode, reason = effective_mode(data, NOW)
    assert mode == Mode.EXAM and "in 5 days" in reason
    # 09:30 exam needs a 20-minute grogginess buffer on top of the routine.
    assert exam_wake_time(data, NOW) <= data.profile.usual_wake


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "doze.db")
    db.init_db()
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="demo-calendar-test")
    app.mount("/static", StaticFiles(directory=Path(__file__).resolve().parents[1] / "app" / "static"), name="static")
    app.include_router(routes.router)
    with TestClient(app) as client:
        yield client


def test_load_and_clear_demo_calendar(client):
    assert client.post("/calendar/demo", follow_redirects=False).status_code == 303
    assert any(e.category == EventCategory.EXAM for e in db.load_calendar_events())
    page = client.get("/calendar").text
    assert "Linear Algebra exam" in page and 'action="/calendar/demo/clear"' in page
    assert client.post("/calendar/demo/clear", follow_redirects=False).status_code == 303
    assert db.load_calendar_events() == []
    page = client.get("/calendar").text
    assert "Linear Algebra exam" not in page
    # The connect button loads the sample calendar rather than starting Google login.
    assert 'action="/calendar/demo"' in page and 'href="/auth/google"' not in page
