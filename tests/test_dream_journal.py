"""Dream journal: saving, Doze's fallback postcard, isolation, delete and migration."""

from datetime import datetime
import sqlite3

from fastapi.testclient import TestClient
from web_helpers import authenticated_client
import pytest

from app import create_app, web
from app.schemas import SleepSession
from app.services import accounts, db, dream_insights

NOW = datetime(2026, 11, 16, 9)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "dreams.db")
    monkeypatch.setattr(web, "local_now", lambda: NOW)
    monkeypatch.setenv("SESSION_SECRET", "dream-tests-only-secret")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)  # exercise Doze's own fallback
    with authenticated_client(create_app(load_environment=False)) as client:
        yield client


def test_old_dreams_table_gains_columns_and_keeps_entries(tmp_path, monkeypatch):
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE dreams (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, "
                     "title TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
        conn.execute("INSERT INTO dreams (user_id, title, content) VALUES ('u1', 'Old', 'An old dream')")
    monkeypatch.setattr(db, "DB_PATH", path)
    db.init_db()
    with sqlite3.connect(path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(dreams)")}
    assert {"details", "image_path"} <= columns
    assert db.get_dreams("u1")[0]["title"] == "Old"


def test_saving_a_dream_titles_it_and_draws_a_postcard_without_gemini(client):
    result = client.post("/api/dreams", json={"title": "", "content": "I was flying over the ocean towards a mountain."})
    assert result.status_code == 200
    body = result.json()
    assert body["title"].startswith("I was flying over")  # Doze's own title from the first words
    assert body["details"]["mood"] == "adventurous" and body["details"]["source"] == "doze"
    assert all(dream_insights.HEX.fullmatch(c) for c in body["details"]["palette"])
    page = client.get("/dream-journal")
    assert page.status_code == 200
    assert 'class="nav-links"' in page.text  # rendered inside the app layout
    assert "dream-art" in page.text and "adventurous" in page.text


def test_dream_shows_the_night_before_it(client):
    db.upsert_sleep_session("night", SleepSession(start=datetime(2026, 11, 15, 23), end=datetime(2026, 11, 16, 7),
                                                  efficiency=0.9, rating=2), now=NOW)
    when = datetime(2026, 11, 16, 8)
    night = dream_insights.night_before(db.load_user_data().sessions, when)
    assert night is not None and night.rating == 2
    assert dream_insights.night_before(db.load_user_data().sessions, datetime(2026, 11, 17, 12)) is None  # too long after


def test_other_accounts_cannot_see_or_delete_a_dream(client):
    dream_id = client.post("/api/dreams", json={"content": "A quiet garden under the stars."}).json()["id"]
    other = db.create_account("other-dreamer@example.com", accounts.hash_password("Another fixture password 1"), "Other")
    assert db.get_dreams(other["id"]) == []
    assert db.delete_dream(other["id"], dream_id) is False
    assert client.delete(f"/api/dreams/{dream_id}").json() == {"deleted": True}
    assert client.delete(f"/api/dreams/{dream_id}").status_code == 404


def test_model_output_is_validated_before_reaching_styles():
    cleaned = dream_insights._clean({
        "title": "Sky city", "summary": "You floated above a city.", "mood": "calm",
        "tags": ["sky", "city"], "palette": ["#a1b2c3", "red; background:url(x)", "#ffffff"],
        "symbols": ["☁️", "<script>", "🌙"],
    }, "I floated above a city")
    assert cleaned["palette"] == list(dream_insights._STYLE["calm"][0])  # one bad colour: use the safe palette
    assert cleaned["symbols"] == list(dream_insights._STYLE["calm"][1])
    assert cleaned["title"] == "Sky city" and cleaned["mood"] == "calm"
    assert dream_insights._clean({"mood": "furious"}, "monster chased me in the dark")["mood"] == "scary"
