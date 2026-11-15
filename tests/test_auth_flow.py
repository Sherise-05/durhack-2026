"""The actual account, conversation, review and returning-user HTTP journey."""

import base64
from datetime import datetime
import json
import re

from fastapi.testclient import TestClient
import pytest

from app import create_app, web
from app.services import accounts, db, onboarding

PASSWORD = "A fictional account password 2026!"
ANSWER = "I am 22. I sleep at 23:30 and wake at 07:30. I need 45 minutes to get ready. My goal is a steady routine."
REAL_REQUEST = onboarding.request_gemini


@pytest.fixture
def application(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "accounts.db")
    monkeypatch.setenv("SESSION_SECRET", "auth-flow-test-only-secret")
    monkeypatch.setenv("SESSION_HTTPS_ONLY", "false")
    monkeypatch.setenv("GEMINI_API_KEY", "fictional-test-provider")
    monkeypatch.delenv("GOOGLE_REDIRECT_URI", raising=False)
    monkeypatch.setattr(web, "local_now", lambda: datetime(2026, 11, 16, 14))
    accounts._attempts.clear()

    def provider(messages, draft):
        quote = next(item["content"] for item in reversed(messages) if item["role"] == "user")
        patch = {"age_band": "18-25", "usual_bedtime": "23:30", "usual_wake": "07:30",
                 "morning_routine_min": 45, "goal": "a steady routine"}
        return {"reply": "Please review your routine.", "profile_patch": patch,
                "evidence": {field: quote for field in patch}}

    monkeypatch.setattr(onboarding, "request_gemini", provider)
    return create_app(load_environment=False)


def signup(client, email="maya@example.com", name="Maya"):
    response = client.post("/signup", data={"email": email, "name": name, "password": PASSWORD}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/onboarding"


def confirm(client, *, name="Maya", wake="07:30"):
    state = client.post("/onboarding/message", json={"message": ANSWER})
    assert state.status_code == 200 and state.json()["ready"] is True
    review = client.get("/onboarding/review")
    assert review.status_code == 200
    revision = re.search(r'name="revision" value="(\d+)"', review.text).group(1)
    return client.post("/onboarding/confirm", data={
        "name": name, "age_band": "18-25", "usual_bedtime": "23:30", "usual_wake": wake,
        "morning_routine_min": "45", "sleep_latency_min": "15", "chronotype": "neutral",
        "goal": "a steady routine", "revision": revision,
    }, follow_redirects=False)


def cookie_values(client):
    return json.loads(base64.b64decode(client.cookies.get("session").split(".")[0]))


def test_welcome_personalise_and_anonymous_guards(application):
    with TestClient(application) as client:
        welcome = client.get("/")
        assert welcome.status_code == 200
        assert "Personalise my plan" in welcome.text and 'href="/signup"' in welcome.text
        assert "Try a demo" not in welcome.text
        assert 'aria-label="Main navigation"' not in welcome.text
        assert client.get("/signup").status_code == 200
        for path in ("/dashboard", "/profile", "/sleep-history", "/calendar", "/auth/google", "/onboarding"):
            response = client.get(path, follow_redirects=False)
            assert response.status_code == 303 and response.headers["location"].startswith("/login?")
        assert client.get("/api/me/profile").status_code == 401
        assert client.post("/onboarding/message", json={"message": ANSWER}).status_code == 401


def test_new_user_requires_conversation_review_and_explicit_confirmation(application):
    with TestClient(application) as client:
        signup(client)
        assert client.get("/dashboard", follow_redirects=False).headers["location"] == "/onboarding"
        assert client.get("/api/me/dashboard").status_code == 403
        chat = client.get("/onboarding")
        assert "Meet Doze" in chat.text and "Gemini processes your answers" in chat.text
        ready = client.post("/onboarding/message", json={"message": ANSWER})
        assert ready.status_code == 200 and ready.json()["ready"]
        account = db.find_account_by_email("maya@example.com")
        assert account["onboarding_complete"] is False
        assert client.get("/dashboard", follow_redirects=False).headers["location"] == "/onboarding"
        response = confirm(client, wake="07:45")
        assert response.status_code == 303 and response.headers["location"] == "/dashboard"
        dashboard = client.get("/dashboard")
        assert dashboard.status_code == 200 and "Maya" in dashboard.text
        profile = client.get("/api/me/profile").json()
        assert profile["usual_wake"] == "07:45:00"
        assert client.get("/api/me/sleep-history").json()["records"] == []
        assert client.get("/api/me/dashboard").json()["snapshot"]["last_night"] is None
        assert client.get("/onboarding", follow_redirects=False).headers["location"] == "/dashboard"
        assert client.get("/").url.path == "/dashboard"


def test_logout_revokes_cookie_and_returning_login_skips_setup(application):
    with TestClient(application) as client:
        signup(client)
        assert confirm(client).status_code == 303
        signed_cookie = client.cookies.get("session")
        payload = cookie_values(client)
        assert set(payload) == {accounts.SESSION_KEY}
        assert "maya@example.com" not in json.dumps(payload) and PASSWORD not in json.dumps(payload)
        assert client.post("/logout", follow_redirects=False).headers["location"] == "/"
        client.cookies.clear()
        client.cookies.set("session", signed_cookie)
        assert client.get("/api/me/profile").status_code == 401
        client.cookies.clear()
        response = client.post("/login", data={"email": "MAYA@EXAMPLE.COM", "password": PASSWORD}, follow_redirects=False)
        assert response.status_code == 303 and response.headers["location"] == "/dashboard"
        assert client.get("/dashboard").status_code == 200


def test_incomplete_account_resumes_its_saved_conversation(application):
    with TestClient(application) as client:
        signup(client)
        response = client.post("/onboarding/message", json={"message": ANSWER})
        transcript = response.json()["messages"]
        client.post("/logout")
        response = client.post("/login", data={"email": "maya@example.com", "password": PASSWORD}, follow_redirects=False)
        assert response.headers["location"] == "/onboarding"
        page = client.get("/onboarding")
        assert ANSWER in page.text
        with db.account_scope(db.find_account_by_email("maya@example.com")["id"]):
            assert db.load_onboarding(db.current_account_id())["messages"] == transcript


def test_two_accounts_keep_profiles_sleep_and_legacy_data_separate(application):
    with db.get_connection() as connection:
        connection.execute("INSERT INTO sleep_history (date,duration,quality) VALUES ('2026-11-15',8,'good')")
    with TestClient(application) as alice, TestClient(application) as bob:
        signup(alice, "alice@example.com", "Alice")
        assert confirm(alice, name="Alice").status_code == 303
        signup(bob, "bob@example.com", "Bob")
        assert confirm(bob, name="Bob").status_code == 303
        one = {"start": "2026-11-15T23:00Z", "end": "2026-11-16T07:00Z", "efficiency": 0.9, "rating": 2}
        assert alice.put("/api/me/sleep-sessions/watch-1", json=one).status_code == 200
        assert bob.get("/api/me/sleep-history").json()["records"] == []
        assert bob.put("/api/me/sleep-sessions/watch-1", json={**one, "start": "2026-11-16T01:00Z"}).status_code == 200
        assert alice.get("/api/me/sleep-history").json()["records"][0]["hours_asleep"] == 7.2
        assert bob.get("/api/me/sleep-history").json()["records"][0]["hours_asleep"] == 5.4
        assert alice.get("/api/me/profile").json()["name"] == "Alice"
        assert bob.get("/api/me/profile").json()["name"] == "Bob"
        assert alice.get("/api/me/sleep-history").json()["legacy_records"] == []
        assert bob.get("/api/me/sleep-history").json()["legacy_records"] == []
        assert alice.delete("/api/me/sleep-sessions/watch-1").status_code == 200
        assert len(bob.get("/api/me/sleep-history").json()["records"]) == 1
    assert db.current_account_id() is None
    assert len(db.fetch_sleep_history()) == 1


@pytest.mark.parametrize("next_path", ["https://evil.example", "//evil.example", "/\\evil.example", "/%2f/evil.example", "/%255cevil.example"])
def test_login_rejects_external_or_obfuscated_redirects(application, next_path):
    with TestClient(application) as client:
        signup(client)
        assert confirm(client).status_code == 303
        client.post("/logout")
        response = client.post("/login", data={"email": "maya@example.com", "password": PASSWORD, "next": next_path}, follow_redirects=False)
        assert response.headers["location"] == "/dashboard"


def test_bad_passwords_duplicates_and_cross_origin_posts(application):
    with TestClient(application) as client:
        assert client.post("/signup", data={"name": "Maya", "email": "maya@example.com", "password": "short"}).status_code == 422
        oversized = "private-password-" * 20
        too_long = client.post("/signup", data={"name": "Maya", "email": "maya@example.com", "password": oversized})
        assert too_long.status_code == 422 and oversized not in too_long.text
        signup(client)
        client.post("/logout")
        duplicate = client.post("/signup", data={"name": "Other", "email": "MAYA@example.com", "password": PASSWORD})
        assert duplicate.status_code == 422
        assert PASSWORD not in duplicate.text
        for _ in range(10):
            assert client.post("/login", data={"email": "maya@example.com", "password": "wrong"}).status_code == 401
        assert client.post("/login", data={"email": "maya@example.com", "password": "wrong"}).status_code == 429
        assert client.post("/signup", headers={"origin": "https://evil.example"}, data={"name": "Other", "email": "other@example.com", "password": PASSWORD}).status_code == 403


def test_expired_server_session_rejects_a_still_signed_cookie(application):
    with TestClient(application) as client:
        signup(client)
        assert confirm(client).status_code == 303
        signed_cookie = client.cookies.get("session")
        token = cookie_values(client)[accounts.SESSION_KEY]
        with db.get_connection() as connection:
            connection.execute("UPDATE account_sessions SET expires_at = ? WHERE token_hash = ?",
                               ("2000-01-01T00:00:00+00:00", accounts.token_hash(token)))
        assert client.get("/api/me/profile").status_code == 401
        assert client.get("/dashboard", follow_redirects=False).headers["location"].startswith("/login?")
        client.cookies.clear()
        client.cookies.set("session", signed_cookie)
        assert client.get("/api/me/profile").status_code == 401


def test_provider_unavailable_keeps_draft_and_manual_confirmation_works(application, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(onboarding, "request_gemini", REAL_REQUEST)
    with TestClient(application) as client:
        signup(client)
        response = client.post("/onboarding/message", json={"message": ANSWER})
        assert response.status_code == 503 and response.json()["error"]
        assert client.get("/api/me/profile").status_code == 403
        review = client.get("/onboarding/review")
        assert review.status_code == 200 and "Let’s set up your routine" in review.text
        assert 'value="23:30' not in review.text
        response = client.post("/onboarding/confirm", data={"name": "Maya", "age_band": "18-25", "usual_bedtime": "23:30", "usual_wake": "07:30", "morning_routine_min": "45"}, follow_redirects=False)
        assert response.status_code == 303
        assert client.get("/api/me/sleep-history").json()["records"] == []


def test_loopback_host_is_chosen_before_login_cookie(application, monkeypatch):
    monkeypatch.setenv("GOOGLE_REDIRECT_URI", "http://localhost:8000/auth/callback")
    with TestClient(application, base_url="http://127.0.0.1:8000") as client:
        response = client.get("/", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "http://localhost:8000/"
        assert "session" not in client.cookies
