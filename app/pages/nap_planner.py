"""The nap button: validate a self-report and pass real inputs to the brain."""

from datetime import timedelta

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict

from app import web
from app.schemas import LocalDatetime, Tiredness
from app.services import db, nap_now
from app.services.nap_recc import recommend_nap
from app.services.sleep_model import PersonModel
from app.time_utils import instant, local_isoformat

router = APIRouter()


class NapRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tiredness: Tiredness | None = None


def _recommendation(tiredness: Tiredness | None):
    data = db.load_user_data()
    now = web.local_now()
    model = PersonModel(data, now)
    return data, recommend_nap(data, now, tiredness=tiredness, model=model)


@router.get("/nap-planner", response_class=HTMLResponse)
def nap_page(request: Request):
    data = db.load_user_data()
    return web.templates.TemplateResponse(
        request=request,
        name="nap_planner.html",
        context={"plan": None, "tiredness": None, "has_sleep_history": bool(data.sessions)},
    )


@router.post("/nap-planner", response_class=HTMLResponse)
def nap_form(request: Request, tiredness: Tiredness = Form(...)):
    data, plan = _recommendation(tiredness)
    return web.templates.TemplateResponse(
        request=request,
        name="nap_planner.html",
        context={
            "plan": plan,
            "tiredness": tiredness.value,
            "has_sleep_history": bool(data.sessions),
            "curves": {
                "With nap": [point.model_dump(mode="json") for point in plan.with_nap],
                "Without nap": [point.model_dump(mode="json") for point in plan.without_nap],
            },
        },
    )


@router.post("/api/me/nap-recommendation")
def nap_api(payload: NapRequest):
    _, plan = _recommendation(payload.tiredness)
    return plan.model_dump(mode="json")


def _nap_choice(data, now, model, start: str | None, insist: bool = False):
    if start == "now" and insist:  # "Nap anyway": the user insists after Doze said no
        return nap_now.insist_now(data, now, model=model)
    if start == "now":  # "Nap now": start immediately
        return nap_now.start_now(data, now, model=model)
    return nap_now.choose_nap(data, now, model=model)  # best time today


@router.post("/api/me/nap-now")
def nap_now_api(start: str | None = None, insist: bool = False):
    """The nap buttons: best time today, (start=now) a nap starting now, or (insist) a nap anyway."""
    data = db.load_user_data()
    now = web.local_now()
    status, plan = _nap_choice(data, now, PersonModel(data, now), start, insist)
    return nap_now.plan_payload(status, plan)


@router.post("/api/me/nap-now/reason")
def nap_reason_api(start: str | None = None, insist: bool = False):
    """A short explanation of the same choice (Gemini, or Doze's own words)."""
    data = db.load_user_data()
    now = web.local_now()
    model = PersonModel(data, now)
    status, plan = _nap_choice(data, now, model, start, insist)
    return nap_now.explain(data, now, status, plan, model=model)


class NapWhatIf(BaseModel):
    model_config = ConfigDict(extra="forbid")
    at: LocalDatetime


@router.post("/api/me/nap-what-if")
def nap_what_if_api(payload: NapWhatIf):
    """Today's nap "What if…" chips: a nap at this time, against no nap. Nothing is saved."""
    data = db.load_user_data()
    now = web.local_now()
    if instant(payload.at) < instant(now) - timedelta(minutes=5) or payload.at.date() != now.date():
        raise HTTPException(status_code=422, detail="Choose a time later today.")
    result = nap_now.nap_what_if(data, now, payload.at, model=PersonModel(data, now))
    for key in ("lie_down", "alarm"):
        result[key] = local_isoformat(result[key]) if result[key] is not None else None
    result["events"] = [{**e, "start": local_isoformat(e["start"])} for e in result["events"]]
    return result
