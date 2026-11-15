"""Completed sleep entry, history and local profile endpoints."""

from __future__ import annotations

from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Body, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field, ValidationError

from app import web
from app.schemas import SleepSession, UserProfile
from app.services import db, learning, sleep_insights
from app.services.modes import MODES, effective_mode
from app.services.sleep_model import nightly_stats, sleep_debt_h

router = APIRouter()


def _validation_error(exc: ValueError) -> HTTPException:
    detail = exc.errors(include_context=False, include_url=False) if isinstance(exc, ValidationError) else str(exc)
    return HTTPException(status_code=422, detail=detail)


def _history_context(*, error: str | None = None, editing: dict | None = None) -> dict:
    data = db.load_user_data()
    now = web.local_now()
    personal = learning.personalise(data, now)
    nightly = nightly_stats(data.sessions, personal.need_h, now)
    debt = sleep_debt_h(data.sessions, personal.need_h, now)
    return {
        "records": db.list_sleep_records(),
        "legacy_records": db.fetch_sleep_history(),
        "nightly": nightly,
        "insights": sleep_insights.build(nightly, personal.need_h, debt, now.date()),
        "debt_h": debt,
        "personal": personal,
        "profile": data.profile,
        "editing": editing or {},
        "error": error,
    }


@router.get("/sleep-history", response_class=HTMLResponse)
def sleep_history(request: Request, edit: str | None = None):
    editing = None
    if edit is not None:
        editing = next((row for row in db.list_sleep_records() if row["source_id"] == edit), None)
        if editing is None:
            raise HTTPException(404, "Sleep record not found")
    return web.templates.TemplateResponse(request=request, name="sleep_history.html",
                                          context=_history_context(editing=editing))


@router.post("/sleep-history", response_class=HTMLResponse)
def save_sleep_form(
    request: Request,
    start: Annotated[str, Form()],
    end: Annotated[str, Form()],
    efficiency: Annotated[float, Form()] = 0.9,
    rating: Annotated[str, Form()] = "",
    is_nap: Annotated[bool, Form()] = False,
    episode_id: Annotated[str, Form()] = "",
    source_id: Annotated[str, Form()] = "",
    hrv_ms: Annotated[str, Form()] = "",
    latency_min: Annotated[str, Form()] = "",
    return_to: Annotated[str, Form(alias="next")] = "",
):
    values = {"start": start, "end": end, "efficiency": efficiency, "is_nap": is_nap,
              "rating": rating or None, "episode_id": episode_id.strip() or None,
              "hrv_ms": hrv_ms or None, "latency_min": latency_min or None}
    try:
        previous = next((row for row in db.list_sleep_records() if row["source_id"] == source_id), {})
        session = SleepSession.model_validate({**previous, **values})
        db.upsert_sleep_session(source_id or uuid4().hex, session, now=web.local_now())
    except ValueError as exc:
        errors = exc.errors(include_context=False, include_url=False) if isinstance(exc, ValidationError) else None
        message = "; ".join(error["msg"] for error in errors) if errors else str(exc)
        editing = {**values, "source_id": source_id}
        return web.templates.TemplateResponse(request=request, name="sleep_history.html",
                                              context=_history_context(error=message, editing=editing), status_code=422)
    # The Today check-in returns to Today; everything else stays on My sleep.
    return RedirectResponse("/dashboard" if return_to == "/dashboard" else "/sleep-history", status_code=303)


@router.post("/sleep-history/{source_id}/delete")
def delete_sleep_form(source_id: str):
    if not db.delete_sleep_session(source_id):
        raise HTTPException(404, "Sleep record not found")
    return RedirectResponse("/sleep-history", status_code=303)


@router.get("/api/me/sleep-history")
def sleep_history_api():
    data = db.load_user_data()
    now = web.local_now()
    personal = learning.personalise(data, now)
    return {
        "records": db.list_sleep_records(),
        "legacy_records": db.fetch_sleep_history(),
        "nightly": [row.model_dump(mode="json") for row in nightly_stats(data.sessions, personal.need_h, now)],
        "personal": {key: value.model_dump(mode="json") if hasattr(value, "model_dump") else value
                     for key, value in vars(personal).items()},
    }


@router.put("/api/me/sleep-sessions/{source_id}")
def put_sleep_session(source_id: str, session: SleepSession):
    try:
        return db.upsert_sleep_session(source_id, session, now=web.local_now())
    except ValueError as exc:
        raise _validation_error(exc) from exc


@router.delete("/api/me/sleep-sessions/{source_id}")
def delete_sleep_session(source_id: str):
    if not db.delete_sleep_session(source_id):
        raise HTTPException(404, "Sleep record not found")
    return {"deleted": True}


class RatingUpdate(BaseModel):
    rating: int = Field(ge=1, le=5)


@router.patch("/api/me/sleep-sessions/{source_id}/rating")
def update_rating(source_id: str, update: RatingUpdate):
    record = next((row for row in db.list_sleep_records() if row["source_id"] == source_id), None)
    if record is None:
        raise HTTPException(404, "Sleep record not found")
    session = SleepSession.model_validate({**record, "rating": update.rating})
    try:
        return db.upsert_sleep_session(source_id, session, now=web.local_now())
    except ValueError as exc:
        raise _validation_error(exc) from exc


@router.get("/api/me/profile")
def get_profile():
    return db.load_user_data().profile.model_dump(mode="json")


def _profile_context(*, values: dict | None = None, error: str | None = None, saved: bool = False) -> dict:
    data = db.load_user_data()
    mode, reason = effective_mode(data, web.local_now())
    return {
        "profile": data.profile,
        "values": values if values is not None else data.profile.model_dump(mode="json"),
        "active_mode": MODES[mode].label,
        "mode_reason": reason,
        "automatic_mode": mode != data.profile.mode,
        "error": error,
        "saved": saved,
    }


@router.get("/profile", response_class=HTMLResponse)
def profile_page(request: Request, saved: bool = False):
    return web.templates.TemplateResponse(request=request, name="profile.html",
                                          context=_profile_context(saved=saved))


@router.post("/profile", response_class=HTMLResponse)
def save_profile_form(
    request: Request,
    name: Annotated[str, Form()],
    age_band: Annotated[str, Form()],
    chronotype: Annotated[str, Form()],
    usual_bedtime: Annotated[str, Form()],
    usual_wake: Annotated[str, Form()],
    sleep_latency_min: Annotated[float, Form()],
    morning_routine_min: Annotated[int, Form()],
):
    values = {"name": name, "age_band": age_band, "chronotype": chronotype,
              "usual_bedtime": usual_bedtime, "usual_wake": usual_wake,
              "sleep_latency_min": sleep_latency_min, "morning_routine_min": morning_routine_min}
    try:
        current = db.load_user_data().profile.model_dump(mode="json")
        if not name.strip():
            raise ValueError("name must not be empty")
        profile = UserProfile.model_validate({**current, "name": name.strip(), "age_band": age_band,
            "chronotype": chronotype, "usual_bedtime": usual_bedtime, "usual_wake": usual_wake,
            "sleep_latency_min": sleep_latency_min, "morning_routine_min": morning_routine_min})
        db.save_profile(profile)
    except ValueError as exc:
        message = "; ".join(error["msg"] for error in exc.errors()) if isinstance(exc, ValidationError) else str(exc)
        return web.templates.TemplateResponse(request=request, name="profile.html",
                                              context=_profile_context(values=values, error=message), status_code=422)
    return RedirectResponse("/profile?saved=1", status_code=303)


@router.patch("/api/me/profile")
def patch_profile(changes: Annotated[dict, Body()]):
    unknown = set(changes) - set(UserProfile.model_fields)
    if unknown:
        raise HTTPException(422, "Unknown profile fields: " + ", ".join(sorted(unknown)))
    try:
        previous = db.load_user_data().profile
        current = previous.model_dump(mode="json")
        profile = UserProfile.model_validate({**current, **changes})
        if not profile.name.strip():
            raise ValueError("name must not be empty")
        if "travel" in changes and profile.travel is not None:
            # API-created trips use a server-controlled creation date. Editing
            # an existing trip cannot retroactively add preparation days.
            created = previous.travel.planned_from if previous.travel is not None else None
            profile.travel = profile.travel.model_copy(update={"planned_from": created or web.local_now().date()})
        return db.save_profile(profile).model_dump(mode="json")
    except ValueError as exc:
        raise _validation_error(exc) from exc
