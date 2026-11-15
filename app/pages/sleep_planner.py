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


def _page(request, data, plan, *, preview=None, error=None, submitted=None, status_code=200):
    context = {
        "plan": plan,
        "preview": preview,
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
def preview_form(request: Request, bedtime: str = Form(...), alarm: str = Form(...)):
    data, now, model, plan = _bedtime_context()
    try:
        payload = WhatIfRequest(bedtime=bedtime, alarm=alarm)
        preview = _preview(data, now, model, payload)
    except ValidationError:
        return _page(request, data, plan, error="Enter a valid bedtime and wake-up time. Times use Europe/London.", submitted=(bedtime, alarm), status_code=422)
    except ValueError as exc:
        return _page(request, data, plan, error=str(exc), submitted=(bedtime, alarm), status_code=422)
    return _page(request, data, plan, preview=preview)


@router.post("/api/me/what-if")
def preview_api(payload: WhatIfRequest):
    data = db.load_user_data()
    now = web.local_now()
    model = PersonModel(data, now)
    try:
        return _preview(data, now, model, payload).model_dump(mode="json")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
