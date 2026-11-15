"""Startup checks that don't need accounts, network access or the user's database."""

import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime

from fastapi.testclient import TestClient
from web_helpers import authenticated_client

import app
from app.services import db
from app import web


def test_brain_import_does_not_load_web_framework_or_environment():
    result = subprocess.run(
        [sys.executable, "-c", "import sys; import app.schemas; assert 'fastapi' not in sys.modules; assert 'dotenv' not in sys.modules"],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_local_session_secret_survives_restart_without_env_secret(tmp_path, monkeypatch):
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    monkeypatch.setattr(app, "PROJECT_ROOT", tmp_path)
    first = app._session_secret()
    assert len(first) >= 48
    assert app._session_secret() == first
    if os.name == "posix":
        assert (tmp_path / "instance" / "session_secret").stat().st_mode & 0o777 == 0o600


def test_app_loads_pages_and_static_files_from_another_working_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "test-only-secret")
    monkeypatch.setenv("SESSION_HTTPS_ONLY", "false")
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.chdir(tmp_path)
    with authenticated_client(app.create_app(load_environment=False)) as client:
        assert client.get("/health").json() == {"status": "ok"}
        for path in ("/dashboard", "/calendar", "/nap-planner", "/sleep-planner", "/sleep-history"):
            response = client.get(path)
            assert response.status_code == 200, (path, response.text)
            assert "text/html" in response.headers["content-type"]
            assert "<nav" in response.text
        assert client.get("/static/css/scripts.css").status_code == 200


def test_other_websites_cannot_modify_local_sleep_records(tmp_path, monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "test-only-secret")
    monkeypatch.setenv("SESSION_HTTPS_ONLY", "false")
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(web, "local_now", lambda: datetime(2026, 11, 16, 14))
    form = {"source_id": "night-1", "start": "2026-11-15T23:00Z", "end": "2026-11-16T07:00Z"}
    with authenticated_client(app.create_app(load_environment=False)) as client:
        for headers in ({"origin": "https://another-site.example"}, {"origin": "null"}, {"sec-fetch-site": "cross-site"}):
            assert client.post("/sleep-history", data=form, headers=headers).status_code == 403
        assert db.load_user_data().sessions == []
        assert client.post("/sleep-history", data=form, headers={"origin": "http://testserver:80"}, follow_redirects=False).status_code == 303
        assert len(db.load_user_data().sessions) == 1
        assert client.post("/sleep-history/night-1/delete", headers={"origin": "https://another-site.example"}).status_code == 403
        assert len(db.load_user_data().sessions) == 1
        assert client.delete("/api/me/sleep-sessions/night-1").status_code == 200
