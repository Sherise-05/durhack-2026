"""Demo modes: each button signs in to a persona's demo account with fake data."""

from datetime import datetime

import pytest
from web_helpers import authenticated_client

from app import create_app, web
from app.schemas import SleepSession
from app.services import db, demo_modes

NOW = datetime(2026, 11, 16, 10)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "demo.db")
    monkeypatch.setattr(web, "local_now", lambda: NOW)
    monkeypatch.setenv("SESSION_SECRET", "demo-mode-tests-only-secret")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with authenticated_client(create_app(load_environment=False)) as client:
        yield client


@pytest.mark.parametrize("mode, shown", [
    ("normal", None), ("exam", "Exam mode is on"), ("holiday", "Holiday mode"), ("travel", "Travel mode"),
])
def test_each_demo_mode_shows_its_mode_on_today(client, mode, shown):
    response = client.post(f"/demo/{mode}", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/dashboard"
    page = client.get("/dashboard")
    card = page.context["mode_card"]
    if shown is None:
        assert card is None
    else:
        assert shown in card["reason"]
        assert "mode-card" in page.text
    if mode in ("holiday", "travel"):
        assert card["plan"] is not None and card["plan"].days  # the day-by-day plan is shown
    if mode == "exam":
        assert card["exams"] and card["fixed_wake"] is not None


def test_demo_never_changes_the_real_account(client):
    real_id = client.account_id
    with db.account_scope(real_id):
        db.upsert_sleep_session("mine", SleepSession(start=datetime(2026, 11, 15, 23), end=datetime(2026, 11, 16, 7),
                                                     efficiency=0.9), now=NOW)
    client.post("/demo/travel")
    client.post("/demo/travel")  # reloading reuses and refreshes the same demo account
    with db.account_scope(real_id):
        assert [r["source_id"] for r in db.list_sleep_records()] == ["mine"]
    assert db.find_account_by_email(demo_modes.demo_email("travel"))["name"]
    assert client.post("/demo/nonsense").status_code == 404


@pytest.mark.parametrize("mode", ["normal", "exam", "holiday", "travel"])
def test_demo_students_have_a_filled_in_journal(client, mode):
    client.post(f"/demo/{mode}")
    client.post(f"/demo/{mode}")  # reloading doesn't duplicate entries
    page = client.get("/dream-journal")
    assert page.status_code == 200
    ctx = page.context
    assert len(ctx["dreams"]) >= 1 and len(ctx["days"]) == 1
    assert any(d["night"] for d in ctx["dreams"])  # linked to a logged night
    if mode in ("exam", "travel"):
        dump = ctx["dumps"][0]["details"]
        assert len(ctx["dumps"]) == 1 and dump["plan"]["scheduled"] and dump["worries"]
    else:
        assert ctx["dumps"] == []
