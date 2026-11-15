"""Spoken wake-up messages: random message and voice, with a browser-voice fallback.

ElevenLabs is faked here, so the tests never generate real speech.
"""

from datetime import datetime

import pytest
from web_helpers import authenticated_client

from app import create_app, web
from app.services import db, wake_voice


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "voice.db")
    monkeypatch.setattr(web, "local_now", lambda: datetime(2026, 11, 16, 7))
    monkeypatch.setenv("SESSION_SECRET", "wake-voice-tests-only-secret")
    with authenticated_client(create_app(load_environment=False)) as client:
        yield client


def test_messages_are_picked_from_the_list_and_personalised():
    for _ in range(20):
        sleep = wake_voice.pick_message("Maya", "sleep")
        nap = wake_voice.pick_message("Maya", "nap")
        assert "Maya" in sleep and "Maya" in nap
        assert sleep in [m.format(name="Maya") for m in wake_voice.WAKE_MESSAGES]
        assert nap in [m.format(name="Maya") for m in wake_voice.NAP_MESSAGES]
    assert "there" in wake_voice.pick_message("", "sleep")


def test_speak_uses_a_random_premade_voice(monkeypatch):
    calls = {}

    class Voice:
        def __init__(self, voice_id, name, category):
            self.voice_id, self.name, self.category = voice_id, name, category

    class FakeClient:
        class voices:
            @staticmethod
            def get_all():
                return type("R", (), {"voices": [Voice("a", "Alice", "premade"), Voice("b", "Bob", "premade"),
                                                 Voice("c", "Clone", "cloned")]})()

        class text_to_speech:
            @staticmethod
            def convert(**kwargs):
                calls.update(kwargs)
                return iter([b"ID3", b"audio"])

    monkeypatch.setattr(wake_voice, "_client", lambda: FakeClient())
    monkeypatch.setattr(wake_voice, "_voice_cache", None)
    audio, voice = wake_voice.speak("Wake up!")
    assert audio == b"ID3audio" and voice in ("Alice", "Bob")  # never the cloned voice
    assert calls["voice_id"] in ("a", "b") and calls["model_id"] == "eleven_v4" and calls["text"] == "Wake up!"


def test_route_returns_mp3_or_the_message_for_a_browser_voice(client, monkeypatch):
    monkeypatch.setattr(wake_voice, "speak", lambda text: (b"ID3fake", "Alice"))
    ok = client.get("/api/me/wake-voice?kind=nap")
    assert ok.status_code == 200 and ok.headers["content-type"] == "audio/mpeg" and ok.content == b"ID3fake"
    assert ok.headers["X-Wake-Voice"] == "Alice" and ok.headers["X-Wake-Message"]

    def unavailable(text):
        raise wake_voice.WakeVoiceUnavailable("no key")

    monkeypatch.setattr(wake_voice, "speak", unavailable)
    fallback = client.get("/api/me/wake-voice")
    assert fallback.status_code == 503 and fallback.json()["message"]


def test_demo_timer_is_on_by_default_and_can_be_switched_off(client, monkeypatch):
    monkeypatch.delenv("DEMO_TIMER_SECONDS", raising=False)
    assert 'data-demo-timer="10"' in client.get("/dashboard").text
    monkeypatch.setenv("DEMO_TIMER_SECONDS", "0")
    assert "data-demo-timer" not in client.get("/dashboard").text
