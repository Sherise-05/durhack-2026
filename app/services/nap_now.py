"""The one-tap nap button: pick the best nap time today, then explain it.

Choosing the time is pure maths (nap_recc). Gemini only rewords the facts the
maths produced, plus today's calendar, into a short explanation. If Gemini is
unavailable the same facts are written up by Doze, so a reason always appears.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import json
import logging
import os
import re

import httpx

from app.schemas import NapRecommendation, Tiredness, UserData
from app.services import planner
from app.services.modes import MODES, effective_mode
from app.services.nap_recc import best_nap_slot, recommend_nap
from app.services.sleep_model import PersonModel
from app.time_utils import instant

log = logging.getLogger(__name__)
NOW_TOLERANCE = timedelta(minutes=5)  # a slot this close to now counts as "nap now"
DEFAULT_GEMINI_MODEL = "gemini-flash-lite-latest"  # fast; 2.5-flash is closed to new users


def choose_nap(data: UserData, now: datetime, model: PersonModel | None = None) -> tuple[str, NapRecommendation]:
    """Return ("later" | "now" | "none", plan).

    1. The best free moment in this afternoon's dip (best_nap_slot), if one helps.
    2. Otherwise a nap right now, if it helps someone who pressed "I'm tired".
    3. Otherwise no nap; the plan still carries an optional alarm if one is possible.
    """
    model = model or PersonModel(data, now)
    slot = best_nap_slot(data, now, model=model)
    if slot is not None:
        later = instant(slot.lie_down) - instant(now) > NOW_TOLERANCE
        return ("later" if later else "now"), slot
    plan = recommend_nap(data, now, tiredness=Tiredness.TIRED, model=model)
    return ("now" if plan.should_nap else "none"), plan


NAP_ANYWAY_LATEST_H = 18  # our choice: never offer an optional nap in the evening


def plan_payload(status: str, plan: NapRecommendation) -> dict:
    data = plan.model_dump(mode="json", exclude={"with_nap", "without_nap"})
    can_nap_anyway = status == "none" and plan.alarm is not None and plan.lie_down.hour < NAP_ANYWAY_LATEST_H
    return {"status": status, "can_nap_anyway": can_nap_anyway, **data}


def _events_today(data: UserData, now: datetime) -> list[dict]:
    end_of_tomorrow_morning = datetime.combine(now.date() + timedelta(days=1), datetime.min.time()) + timedelta(hours=12)
    rows = []
    for event in sorted(data.events, key=lambda e: instant(e.start)):
        if instant(event.end) <= instant(now) or instant(event.start) >= instant(end_of_tomorrow_morning):
            continue
        rows.append({
            "title": event.title,
            "when": f"{event.start:%a %H:%M}-{event.end:%H:%M}",
            "type": event.category.value,
        })
    return rows[:8]


def _next_after(data: UserData, when: datetime) -> str | None:
    nxt = min((e for e in data.events if instant(e.start) >= instant(when) and e.category.value != "rest"),
              key=lambda e: instant(e.start), default=None)
    if nxt is None:
        return None
    days = (nxt.start.date() - when.date()).days
    if days > 1:  # too far ahead to explain today's nap
        return None
    return f"{nxt.title} at {nxt.start:%H:%M}{' tomorrow' if days == 1 else ''}"


def nap_facts(data: UserData, now: datetime, status: str, plan: NapRecommendation, model: PersonModel | None = None) -> dict:
    model = model or PersonModel(data, now)
    bedtime = planner.recommend_bedtime(data, now, model=model)
    mode, mode_reason = effective_mode(data, now)
    last = model.last_night
    return {
        "now": f"{now:%a %H:%M}",
        "decision": {
            "later": "nap later today at the best moment",
            "now": "nap now",
            "none": "no nap recommended",
        }[status],
        "lie_down": f"{plan.lie_down:%H:%M}",
        "alarm": f"{plan.alarm:%H:%M}" if plan.alarm else None,
        "nap_minutes": plan.sleep_min or None,
        "alertness_now_0_100": round(plan.alertness_now) if plan.alertness_now is not None else None,
        "alertness_after_0_100": round(plan.alertness_after) if plan.alertness_after is not None else None,
        "share_of_tonights_sleep_pressure_used_pct": round(plan.tonight_cost_pct),
        "headline": plan.headline,
        "doze_reasons": plan.reasons + plan.warnings,
        "next_event_after_nap": _next_after(data, plan.alarm) if plan.alarm else None,
        "events_today_and_tomorrow_morning": _events_today(data, now),
        "tonight": {"lights_out": f"{bedtime.bedtime:%H:%M}", "wake": f"{bedtime.alarm:%H:%M}"},
        "mode": MODES[mode].label + (f" ({mode_reason})" if mode_reason else ""),
        "last_night_hours_asleep": round(last.hours_asleep, 1) if last else None,
        "sleep_need_hours": round(model.need_h, 1),
        "sleep_debt_hours": round(model.debt_h, 1),
    }


def fallback_reason(facts: dict, status: str) -> str:
    """Doze's own short explanation from the same facts (no AI)."""
    parts = []
    if status == "later":
        parts.append(f"{facts['lie_down']} is your best moment today: it's when your alertness is predicted to dip lowest in a free gap.")
    elif status == "now":
        parts.append(f"A {facts['nap_minutes']}-minute nap now is predicted to lift your alertness"
                     + (f" from {facts['alertness_now_0_100']} to {facts['alertness_after_0_100']}." if facts["alertness_after_0_100"] else "."))
    else:
        # The headline carries the real reason ("Too close to bedtime..."); drop the optional-alarm tail.
        reason = facts["headline"].split(". If you want one", 1)[0].rstrip(".")
        parts.append(reason + ".")
        daylight = next((r for r in facts["doze_reasons"] if "walk in daylight" in r), None)
        if daylight:
            parts.append(daylight)
    if facts["next_event_after_nap"] and status != "none":
        parts.append(f"You'll be awake and past the grogginess before {facts['next_event_after_nap']}.")
    if status != "none":
        parts.append(f"It uses about {facts['share_of_tonights_sleep_pressure_used_pct']}% of tonight's sleep pressure, so lights out can stay {facts['tonight']['lights_out']}.")
    return " ".join(parts)


def _gemini_reason(facts: dict) -> str | None:
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        return None
    model = os.getenv("GEMINI_MODEL", "").strip() or DEFAULT_GEMINI_MODEL
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", model):
        return None
    system = (
        "You explain a sleep app's nap decision to a university student. Write 2-3 short, warm sentences "
        "in the second person. Say why this time was chosen and, where relevant, mention today's events "
        "by name (for example a lecture or exam afterwards). Use ONLY the facts provided; never invent "
        "numbers, times, events, activities or feelings (don't say how the user feels or what they are "
        "doing unless a fact says so; keep 'if' advice conditional), and don't recalculate anything. If the decision is 'no nap "
        "recommended', explain why and suggest one gentle alternative from the reasons. No medical "
        "advice, no diagnoses, no emojis, no markdown."
    )
    payload = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": "Facts:\n" + json.dumps(facts, ensure_ascii=False)}]}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 400},
    }
    try:
        with httpx.Client(timeout=httpx.Timeout(12, connect=5)) as client:
            response = client.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                headers={"x-goog-api-key": key}, json=payload,
            )
        response.raise_for_status()
        parts = response.json()["candidates"][0]["content"]["parts"]
        text = " ".join(part.get("text", "") for part in parts if not part.get("thought")).strip()
        return text[:900] or None
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
        # Never log the key or request; the status code is enough to debug.
        status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else type(exc).__name__
        log.warning("Gemini nap explanation unavailable (%s); using Doze's own reason.", status)
        return None


def explain(data: UserData, now: datetime, status: str, plan: NapRecommendation, model: PersonModel | None = None) -> dict:
    facts = nap_facts(data, now, status, plan, model=model)
    text = _gemini_reason(facts)
    if text:
        return {"reason": text, "source": "gemini"}
    return {"reason": fallback_reason(facts, status), "source": "doze"}
