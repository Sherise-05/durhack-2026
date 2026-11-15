"""Signed-in first-use conversation and explicit profile review."""

from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field

from app import web
from app.services import onboarding as service

router = APIRouter()


def _account(request: Request) -> dict:
    account = getattr(request.state, "account", None)
    if not account:
        raise HTTPException(status_code=401, detail="Sign in to personalise your plan.")
    return account


def _chat_context(account: dict) -> dict:
    return service.public_state(service.ensure_state(account))


@router.get("/onboarding", response_class=HTMLResponse)
def onboarding_page(request: Request):
    account = _account(request)
    if account.get("onboarding_complete"):
        return RedirectResponse("/dashboard", status_code=303)
    return web.templates.TemplateResponse(request=request, name="onboarding.html", context=_chat_context(account))


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=service.MAX_MESSAGE_LENGTH)


@router.post("/onboarding/message")
def chat_message(request: Request, payload: ChatMessage):
    account = _account(request)
    try:
        return service.converse(account, payload.message)
    except service.OnboardingError as exc:
        if account.get("onboarding_complete"):
            return JSONResponse(status_code=exc.status_code, content={"detail": str(exc), "error": str(exc), "next": "/dashboard"})
        context = _chat_context(account)
        return JSONResponse(status_code=exc.status_code, content={**context, "reply": None, "detail": str(exc), "error": str(exc)})


@router.post("/onboarding/chat", response_class=HTMLResponse)
def chat_form(request: Request, message: str = Form("")):
    account = _account(request)
    if account.get("onboarding_complete"):
        return RedirectResponse("/dashboard", status_code=303)
    try:
        service.converse(account, message)
    except service.OnboardingError as exc:
        return web.templates.TemplateResponse(request=request, name="onboarding.html", context={**_chat_context(account), "error": str(exc), "pending_message": message}, status_code=exc.status_code)
    return RedirectResponse("/onboarding", status_code=303)


@router.get("/onboarding/review", response_class=HTMLResponse)
def review_page(request: Request):
    account = _account(request)
    if account.get("onboarding_complete"):
        return RedirectResponse("/dashboard", status_code=303)
    return web.templates.TemplateResponse(request=request, name="onboarding_review.html", context=service.review_state(account))


@router.post("/onboarding/confirm", response_class=HTMLResponse)
def confirm_page(
    request: Request,
    name: str = Form(""), age_band: str = Form(""),
    usual_bedtime: str = Form(""), usual_wake: str = Form(""),
    morning_routine_min: str = Form(""), sleep_latency_min: str = Form("15"),
    chronotype: str = Form("neutral"), goal: str = Form(""),
    revision: str = Form(""),
):
    account = _account(request)
    if account.get("onboarding_complete"):
        return RedirectResponse("/dashboard", status_code=303)
    values = {"name": name, "age_band": age_band, "usual_bedtime": usual_bedtime,
              "usual_wake": usual_wake, "morning_routine_min": morning_routine_min,
              "sleep_latency_min": sleep_latency_min, "chronotype": chronotype, "goal": goal or None}
    try:
        if revision and not revision.isdecimal():
            raise service.OnboardingError("Refresh the profile review and try again.")
        service.confirm_profile(account, values, revision=int(revision) if revision else None)
    except service.OnboardingError as exc:
        context = {**service.review_state(account), "values": values, "error": str(exc)}
        return web.templates.TemplateResponse(request=request, name="onboarding_review.html", context=context, status_code=exc.status_code)
    return RedirectResponse("/dashboard", status_code=303)
