"""Bedtime recommendations and bounded, explicitly hypothetical previews."""

from datetime import timedelta

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, ValidationError

from app import web
from app.schemas import LocalDatetime
from app.services import db, planner
from app.services.sleep_model import PersonModel
from app.time_utils import hours_between, instant, local_isoformat

router = APIRouter()


class WhatIfRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bedtime: LocalDatetime
    alarm: LocalDatetime


def _bedtime_context():
    data = db.load_user_data()
    now = web.local_now()
    model = PersonModel(data, now)
    plan = planner.recommend_bedtime(data, now, model=model)
    return data, now, model, plan


def _preview(data, now, model, payload: WhatIfRequest):
    if instant(payload.bedtime) > instant(now) + timedelta(days=7):
        raise ValueError("Choose a bedtime within the next seven days.")
    if hours_between(payload.bedtime, payload.alarm) > 24:
        raise ValueError("Preview one sleep window of at most 24 hours.")
    return planner.what_if_bedtime(data, now, payload.bedtime, payload.alarm, model=model)


# One-tap scenarios: (label, hours to move lights-out, hours to move the alarm).
PRESETS = {
    "early": ("Early night", -1.0, 0.0),
    "later": ("Stay up 1 h later", 1.0, 0.0),
    "late": ("Late night (2 a.m.)", None, 0.0),
    "lie_in": ("Lie in 1 h", 0.0, 1.0),
}


def _preset_times(plan, preset: str):
    _, bed_shift, wake_shift = PRESETS[preset]
    if bed_shift is None:  # lights out at 2 a.m. after tonight's planned bedtime
        bed = plan.bedtime.replace(hour=2, minute=0, second=0, microsecond=0)
        if instant(bed) <= instant(plan.bedtime):
            bed += timedelta(days=1)
    else:
        bed = plan.bedtime + timedelta(hours=bed_shift)
    return bed, plan.alarm + timedelta(hours=wake_shift)


def _comparison(data, now, model, plan, preview):
    """Simulate Doze's own plan the same way, so the two can be compared."""
    if preview is None or not plan.feasible or plan.in_progress:
        return None
    try:
        base = planner.what_if_bedtime(data, now, plan.bedtime, plan.alarm, model=model)
    except ValueError:
        return None
    mine = {(e.title, e.start): e.alertness for e in preview.events}
    # Waking without time to get ready means arriving late, whatever the alertness score says.
    ready = preview.alarm + timedelta(minutes=data.profile.morning_routine_min)
    by_key = {(e.title, e.start): e for e in data.events}
    events = []
    for e in base.events:
        event = by_key.get((e.title, e.start))
        late = event is not None and instant(preview.bedtime) < instant(event.start) < instant(ready + timedelta(minutes=event.travel_min))
        events.append({"title": e.title, "start": e.start, "plan": round(e.alertness),
                       "choice": round(mine[(e.title, e.start)]) if (e.title, e.start) in mine else None, "late": late})
    return {
        "sleep_delta_h": preview.sleep_h - base.sleep_h,
        "base_sleep_h": base.sleep_h,
        "events": events,
        "curves": {
            "Doze's plan": [p.model_dump(mode="json") for p in base.curve],
            "Your choice": [p.model_dump(mode="json") for p in preview.curve],
        },
    }


def _page(request, data, plan, *, preview=None, error=None, submitted=None, status_code=200, compare=None, preset=None):
    context = {
        "plan": plan,
        "preview": preview,
        "compare": compare,
        "presets": {key: value[0] for key, value in PRESETS.items()},
        "active_preset": preset,
        "error": error,
        "has_sleep_history": bool(data.sessions),
        "bedtime_input": submitted[0] if submitted is not None else local_isoformat(preview.bedtime if preview is not None else plan.bedtime),
        "alarm_input": submitted[1] if submitted is not None else local_isoformat(preview.alarm if preview is not None else plan.alarm),
    }
    if preview is not None:
        context["curve"] = [point.model_dump(mode="json") for point in preview.curve]
    return web.templates.TemplateResponse(
        request=request,
        name="sleep_planner.html",
        context=context,
        status_code=status_code,
    )


@router.get("/sleep-planner", response_class=HTMLResponse)
def sleep_page(request: Request):
    data, _, _, plan = _bedtime_context()
    return _page(request, data, plan)


@router.get("/api/me/bedtime-recommendation")
def bedtime_api():
    _, _, _, plan = _bedtime_context()
    return plan.model_dump(mode="json")


@router.post("/sleep-planner/what-if", response_class=HTMLResponse)
def preview_form(request: Request, bedtime: str = Form(""), alarm: str = Form(""), preset: str = Form("")):
    data, now, model, plan = _bedtime_context()
    try:
        if preset:
            if preset not in PRESETS or not plan.feasible or plan.in_progress:
                raise ValueError("That scenario isn't available right now. Try custom times instead.")
            bed, wake = _preset_times(plan, preset)
            if instant(bed) < instant(now):
                raise ValueError("That bedtime has already passed. Try a later scenario or custom times.")
            bedtime, alarm = local_isoformat(bed), local_isoformat(wake)
        payload = WhatIfRequest(bedtime=bedtime, alarm=alarm)
        preview = _preview(data, now, model, payload)
    except ValidationError:
        return _page(request, data, plan, error="Enter a valid bedtime and wake-up time. Times use Europe/London.", submitted=(bedtime, alarm), status_code=422)
    except ValueError as exc:
        return _page(request, data, plan, error=str(exc), submitted=(bedtime, alarm), status_code=422)
    return _page(request, data, plan, preview=preview, preset=preset or None,
                 compare=_comparison(data, now, model, plan, preview))


@router.post("/api/me/what-if")
def preview_api(payload: WhatIfRequest):
    data = db.load_user_data()
    now = web.local_now()
    model = PersonModel(data, now)
    try:
        return _preview(data, now, model, payload).model_dump(mode="json")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
