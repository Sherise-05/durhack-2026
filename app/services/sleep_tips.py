"""Three short, personal tips for getting closer to your sleep goal.

The numbers come from the model; Gemini only turns them into friendly advice.
Without Gemini, Doze picks tips from simple rules over the same facts.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import json
import logging
import os
import re

import httpx

from app.schemas import UserData
from app.services import planner
from app.services.modes import MODES, effective_mode
from app.services.sleep_insights import build as build_insights
from app.services.sleep_model import PersonModel, nightly_stats
from app.time_utils import instant

log = logging.getLogger(__name__)
DEFAULT_GEMINI_MODEL = "gemini-flash-lite-latest"


def _fmt(hours: float) -> str:
    minutes = round(abs(hours) * 60)
    h, m = divmod(minutes, 60)
    return f"{h}h {m:02d}m" if h else f"{m} min"


def tip_facts(data: UserData, now: datetime, model: PersonModel | None = None) -> dict:
    model = model or PersonModel(data, now)
    bedtime = planner.recommend_bedtime(data, now, model=model)
    insights = build_insights(nightly_stats(data.sessions, model.need_h, now), model.need_h, model.debt_h, now.date())
    mode, mode_reason = effective_mode(data, now)
    tomorrow = now.date() + timedelta(days=1)
    first_tomorrow = min((e for e in data.events if e.start.date() == tomorrow and e.category.value != "rest"),
                         key=lambda e: instant(e.start), default=None)
    last = model.last_night
    return {
        "time_now": f"{now:%a %H:%M}",
        "sleep_need_hours": round(model.need_h, 1),
        "last_night_hours_asleep": round(last.hours_asleep, 1) if last else None,
        "average_last_7_days_hours": round(insights.recent_avg_h, 1) if insights.recent_avg_h is not None else None,
        "sleep_debt_hours": round(model.debt_h, 1),
        "rhythm": insights.regularity,
        "wake_time_spread_minutes": round(insights.wake_sd_min) if insights.wake_sd_min is not None else None,
        "weekend_shift_minutes": round(insights.social_jet_lag_min) if insights.social_jet_lag_min is not None else None,
        "tonight": {"wind_down": f"{bedtime.wind_down:%H:%M}", "lights_out": f"{bedtime.bedtime:%H:%M}",
                    "wake": f"{bedtime.alarm:%H:%M}", "feasible": bedtime.feasible},
        "first_event_tomorrow": f"{first_tomorrow.title} at {first_tomorrow.start:%H:%M}" if first_tomorrow else None,
        "mode": MODES[mode].label + (f" ({mode_reason})" if mode_reason else ""),
        "nights_logged_last_14_days": insights.nights_logged,
    }


def fallback_tips(f: dict) -> list[str]:
    tips = []
    if f["tonight"]["feasible"]:
        tips.append(f"Start winding down at {f['tonight']['wind_down']}: dim the lights and put your phone away, "
                    f"so you're asleep by about {f['tonight']['lights_out']}.")
    if f["sleep_debt_hours"] >= 1:
        tips.append(f"You're about {_fmt(f['sleep_debt_hours'])} behind. Win it back with slightly earlier nights, "
                    "not one long lie-in.")
    if f["wake_time_spread_minutes"] and f["wake_time_spread_minutes"] > 45:
        tips.append(f"Your wake time moves around by about {_fmt(f['wake_time_spread_minutes'] / 60)}. "
                    f"Try getting up at {f['tonight']['wake']} every day, even weekends.")
    if f["first_event_tomorrow"]:
        tips.append(f"Tomorrow starts with {f['first_event_tomorrow']}. Lay out what you need tonight so your morning is calm.")
    if f["nights_logged_last_14_days"] < 3:
        tips.append("Do the morning check-in each day. After a few nights Doze learns how much sleep you really need.")
    tips.append("Get some daylight soon after waking: morning light helps set your body clock.")
    return tips[:3]


def _gemini(facts: dict) -> list[str] | None:
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        return None
    model = os.getenv("GEMINI_MODEL", "").strip() or DEFAULT_GEMINI_MODEL
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", model):
        return None
    system = (
        "You are Doze, a friendly sleep coach for university students. Give exactly three short, specific tips "
        "(max 25 words each) to help this person reach their sleep goal, using the facts provided. Refer to "
        "their actual times and events. Use only the facts; never invent numbers, events or feelings. No "
        "medical advice, no medication or supplements, no diagnoses, no emojis, no markdown."
    )
    payload = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": "Facts:\n" + json.dumps(facts, ensure_ascii=False)}]}],
        "generationConfig": {"temperature": 0.3, "maxOutputTokens": 400, "responseMimeType": "application/json",
                             "responseJsonSchema": {"type": "object", "required": ["tips"], "properties": {
                                 "tips": {"type": "array", "items": {"type": "string"}}}}},
    }
    try:
        with httpx.Client(timeout=httpx.Timeout(12, connect=5)) as client:
            response = client.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                headers={"x-goog-api-key": key}, json=payload,
            )
        response.raise_for_status()
        parts = response.json()["candidates"][0]["content"]["parts"]
        tips = json.loads("".join(p.get("text", "") for p in parts if not p.get("thought")))["tips"]
        tips = [t.strip() for t in tips if isinstance(t, str) and 0 < len(t.strip()) <= 240][:3]
        return tips or None
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
        status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else type(exc).__name__
        log.warning("Gemini sleep tips unavailable (%s); using Doze's own.", status)
        return None


def tips(data: UserData, now: datetime, model: PersonModel | None = None) -> dict:
    facts = tip_facts(data, now, model=model)
    ai = _gemini(facts)
    return {"tips": ai, "source": "gemini"} if ai else {"tips": fallback_tips(facts), "source": "doze"}
