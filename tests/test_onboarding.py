"""Onboarding stores confirmed inputs without inventing sleep or trusting AI state."""

import json

import httpx
import pytest

from app.services import db, onboarding as service


ANSWER = "I am 20. I go to bed at 22:30 and wake at 07:00. I need 40 minutes to get ready."
FULL_PATCH = {"age_band": "18-25", "usual_bedtime": "22:30", "usual_wake": "07:00", "morning_routine_min": 40}


@pytest.fixture
def account(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "onboarding.db")
    db.init_db()
    return db.create_account("maya@example.test", "test-password-hash", "Maya")


def reply(patch=None, *, text="Thanks, tell me a little more.", evidence=None):
    patch = patch or {}
    return {"reply": text, "profile_patch": patch,
            "evidence": evidence if evidence is not None else {key: ANSWER for key in patch}}


def review_values(**changes):
    return {"name": "Maya", "age_band": "18-25", "usual_bedtime": "22:30", "usual_wake": "07:00",
            "morning_routine_min": "40", "sleep_latency_min": "15", "chronotype": "neutral", "goal": None, **changes}


def test_initial_state_only_knows_signup_name_and_resumes(account):
    first = service.ensure_state(account)
    again = service.ensure_state(account)
    assert first == again
    assert first["draft"] == {"name": "Maya"}
    assert len(first["messages"]) == 1
    public = service.public_state(first)
    assert public["ready"] is False
    assert set(public["missing_fields"]) == set(service.REQUIRED_FIELDS) - {"name"}
    with db.account_scope(account["id"]):
        assert db.load_user_data().sessions == []


def test_ready_draft_is_not_a_saved_profile_until_review_confirmation(account, monkeypatch):
    monkeypatch.setattr(service, "request_gemini", lambda messages, draft: reply(FULL_PATCH))
    result = service.converse(account, ANSWER)
    assert result["ready"] is True
    assert result["missing_fields"] == []
    assert "ready to review" in result["reply"]
    assert db.get_account(account["id"])["onboarding_complete"] is False
    with db.account_scope(account["id"]):
        assert db.load_user_data().profile.usual_bedtime.strftime("%H:%M") != "22:30"
        assert db.load_user_data().sessions == []
    assert service.ensure_state(account)["messages"] == result["messages"]


def test_model_completion_claim_cannot_bypass_required_coverage(account, monkeypatch):
    monkeypatch.setattr(service, "request_gemini", lambda messages, draft: reply(text="You are all set!"))
    result = service.converse(account, "Hello")
    assert result["ready"] is False
    assert "all set" not in result["reply"].lower()
    assert "age band" in result["reply"]


@pytest.mark.parametrize("patch", [
    {"usual_bedtime": "25:30"}, {"usual_wake": "08:30+01:00"},
    {"age_band": "20"}, {"morning_routine_min": -1},
    {"sleep_latency_min": 100}, {"goal": " "},
    {"name": " "}, {"mode": "exam"}, {"sleep_need_h": 2},
])
def test_invalid_or_unsupported_model_fields_do_not_change_draft(account, monkeypatch, patch):
    monkeypatch.setattr(service, "request_gemini", lambda messages, draft: reply(patch))
    with pytest.raises(service.ProviderError):
        service.converse(account, ANSWER)
    state = service.ensure_state(account)
    assert state["draft"] == {"name": "Maya"}
    assert state["pending"] is True
    assert db.get_account(account["id"])["onboarding_complete"] is False


def test_unsupported_quote_rejected_and_retry_preserves_one_user_turn(account, monkeypatch):
    monkeypatch.setattr(service, "request_gemini", lambda messages, draft: reply(FULL_PATCH, evidence={key: "I am 80" for key in FULL_PATCH}))
    with pytest.raises(service.ProviderError):
        service.converse(account, ANSWER)
    assert service.ensure_state(account)["draft"] == {"name": "Maya"}
    monkeypatch.setattr(service, "request_gemini", lambda messages, draft: reply(FULL_PATCH))
    result = service.converse(account, ANSWER)
    assert result["ready"] is True
    assert sum(message["role"] == "user" for message in result["messages"]) == 1


def test_historical_quote_cannot_reverse_a_later_user_correction(account, monkeypatch):
    responses = iter([
        reply({"usual_bedtime": "23:00"}, evidence={"usual_bedtime": "23:00"}),
        reply({"usual_bedtime": "22:00"}, evidence={"usual_bedtime": "22:00"}),
        reply({"usual_bedtime": "23:00", "morning_routine_min": 40},
              evidence={"usual_bedtime": "23:00", "morning_routine_min": "40 minutes"}),
        reply({"morning_routine_min": 40}, evidence={"morning_routine_min": "40 minutes"}),
    ])
    monkeypatch.setattr(service, "request_gemini", lambda *_: next(responses))
    service.converse(account, "I usually go to bed at 23:00.")
    service.converse(account, "Correction: I now go to bed at 22:00.")
    with pytest.raises(service.ProviderError):
        service.converse(account, "I need 40 minutes to get ready.")
    draft = service.ensure_state(account)["draft"]
    assert draft["usual_bedtime"] == "22:00:00"
    assert "morning_routine_min" not in draft
    retried = service.converse(account, "I need 40 minutes to get ready.")
    assert retried["draft"]["usual_bedtime"] == "22:00:00"
    assert retried["draft"]["morning_routine_min"] == 40
    assert sum(item["role"] == "user" for item in retried["messages"]) == 3


def test_earlier_answer_can_fill_a_previously_uncollected_field(account, monkeypatch):
    responses = iter([
        reply({"usual_bedtime": "23:00"}, evidence={"usual_bedtime": "23:00"}),
        reply({"age_band": "18-25", "morning_routine_min": 40},
              evidence={"age_band": "I am 20", "morning_routine_min": "40 minutes"}),
    ])
    monkeypatch.setattr(service, "request_gemini", lambda *_: next(responses))
    service.converse(account, "I am 20 and usually go to bed at 23:00.")
    result = service.converse(account, "I need 40 minutes to get ready.")
    assert result["draft"]["age_band"] == "18-25"
    assert result["draft"]["morning_routine_min"] == 40


def test_provider_outage_keeps_answer_and_never_pretends_ai_responded(account, monkeypatch):
    def fail(messages, draft):
        raise service.ProviderError()
    monkeypatch.setattr(service, "request_gemini", fail)
    with pytest.raises(service.ProviderError):
        service.converse(account, ANSWER)
    result = service.public_state(service.ensure_state(account))
    assert result["messages"][-1] == {"role": "user", "content": ANSWER}
    assert result["pending_message"] == ANSWER
    assert result["error"]
    assert result["ready"] is False


def test_review_defaults_are_explicit_and_required_fields_stay_blank(account):
    result = service.review_state(account)
    assert result["values"]["name"] == "Maya"
    assert result["values"]["age_band"] == ""
    assert result["values"]["usual_bedtime"] == ""
    assert result["values"]["usual_wake"] == ""
    assert result["values"]["morning_routine_min"] == ""
    assert result["values"]["sleep_latency_min"] == 15
    assert result["values"]["chronotype"] == "neutral"
    assert len(result["defaults"]) == 2
    assert result["from_conversation"] is False


def test_manual_review_confirmation_creates_real_profile_without_sleep(account):
    state = service.review_state(account)
    profile = service.confirm_profile(account, review_values(goal="Feel steadier in the afternoon"), revision=state["revision"])
    assert profile.name == "Maya"
    assert profile.usual_bedtime.strftime("%H:%M") == "22:30"
    assert db.get_account(account["id"])["onboarding_complete"] is True
    with db.account_scope(account["id"]):
        saved = db.load_user_data()
        assert saved.profile == profile
        assert saved.sessions == []


def test_stale_review_cannot_overwrite_newer_conversation(account, monkeypatch):
    first = service.review_state(account)
    monkeypatch.setattr(service, "request_gemini", lambda messages, draft: reply(FULL_PATCH))
    service.converse(account, ANSWER)
    with pytest.raises(service.OnboardingError) as error:
        service.confirm_profile(account, review_values(), revision=first["revision"])
    assert error.value.status_code == 409
    assert db.get_account(account["id"])["onboarding_complete"] is False


@pytest.mark.parametrize("changes", [{"age_band": ""}, {"usual_wake": ""}, {"morning_routine_min": ""}, {"name": " "}, {"sleep_latency_min": "91"}])
def test_invalid_review_never_marks_account_complete(account, changes):
    service.ensure_state(account)
    with pytest.raises(service.OnboardingError):
        service.confirm_profile(account, review_values(**changes))
    assert db.get_account(account["id"])["onboarding_complete"] is False


def test_accounts_cannot_read_or_overwrite_each_others_conversation(account, monkeypatch):
    other = db.create_account("leo@example.test", "different-test-hash", "Leo")
    monkeypatch.setattr(service, "request_gemini", lambda messages, draft: reply(FULL_PATCH))
    with db.account_scope(account["id"]):
        service.converse(account, ANSWER)
        with pytest.raises(ValueError):
            service.ensure_state(other)
    with db.account_scope(other["id"]):
        state = service.ensure_state(other)
        assert state["draft"] == {"name": "Leo"}
        assert len(state["messages"]) == 1


def test_length_and_turn_limits_do_not_call_provider(account, monkeypatch):
    monkeypatch.setattr(service, "request_gemini", lambda *_: pytest.fail("Provider should not be called"))
    for text in (" ", "a" * 2001):
        with pytest.raises(service.OnboardingError):
            service.converse(account, text)
    state = service.ensure_state(account)
    state["messages"] = [{"role": "user", "content": "Hello"}, {"role": "assistant", "content": "What is your usual routine?"}] * service.MAX_USER_TURNS
    db.save_onboarding(account["id"], state, expected_revision=state["_revision"])
    with pytest.raises(service.OnboardingError) as error:
        service.converse(account, "Another answer")
    assert error.value.status_code == 429


def test_missing_provider_and_unsafe_model_have_clear_no_network_errors(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(service.ProviderError, match="not configured"):
        service.request_gemini([], {})
    monkeypatch.setenv("GEMINI_API_KEY", "test-only-key")
    monkeypatch.setenv("GEMINI_MODEL", "../../other-host")
    with pytest.raises(service.ProviderError, match="settings"):
        service.request_gemini([], {})


def test_provider_uses_server_header_and_ignores_thought_parts(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-only-key")
    monkeypatch.setenv("GEMINI_MODEL", "")
    captured = {}
    class Client:
        def __init__(self, **kwargs):
            captured["timeout"] = kwargs["timeout"]
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def post(self, url, *, headers, json):
            captured.update(url=url, headers=headers, payload=json)
            data = {"candidates": [{"content": {"parts": [
                {"thought": True, "text": "private provider reasoning"},
                {"text": __import__("json").dumps(reply())},
            ]}}]}
            return httpx.Response(200, json=data, request=httpx.Request("POST", url))
    monkeypatch.setattr(service.httpx, "Client", Client)
    result = service.request_gemini([{"role": "user", "content": "Hello"}], {})
    assert result == reply()
    assert captured["headers"] == {"x-goog-api-key": "test-only-key"}
    assert "/gemini-3.5-flash-lite:generateContent" in captured["url"]
    assert "test-only-key" not in captured["url"]
    assert "private provider reasoning" not in json.dumps(result)
    assert captured["payload"]["generationConfig"]["responseMimeType"] == "application/json"
    assert "thinkingConfig" not in captured["payload"]["generationConfig"]
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.8-flash")
    service.request_gemini([{"role": "user", "content": "Hello"}], {})
    assert "/gemini-3.8-flash:generateContent" in captured["url"]
    assert captured["payload"]["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "low"}


def test_provider_http_errors_do_not_expose_response_or_secrets(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-only-key")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-2.5-flash")
    class Client:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def post(self, url, **kwargs):
            return httpx.Response(500, text="private-token-and-provider-details", request=httpx.Request("POST", url))
    monkeypatch.setattr(service.httpx, "Client", Client)
    with pytest.raises(service.ProviderError) as error:
        service.request_gemini([{"role": "user", "content": "Hello"}], {})
    assert "private-token" not in str(error.value)


def test_retired_provider_model_has_specific_sanitized_configuration_error(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-only-key")
    monkeypatch.setenv("GEMINI_MODEL", "retired-model")
    class Client:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def post(self, url, **kwargs):
            return httpx.Response(404, text="private-provider-error-with-key", request=httpx.Request("POST", url))
    monkeypatch.setattr(service.httpx, "Client", Client)
    with pytest.raises(service.ProviderError) as error:
        service.request_gemini([{"role": "user", "content": "Hello"}], {})
    assert "update GEMINI_MODEL" in str(error.value)
    assert "private-provider" not in str(error.value)
