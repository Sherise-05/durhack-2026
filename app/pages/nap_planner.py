"""The nap button: validate a self-report and pass real inputs to the brain."""

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict

from app import web
from app.schemas import Tiredness
from app.services import db, nap_now
from app.services.nap_recc import recommend_nap
from app.services.sleep_model import PersonModel

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


@router.post("/api/me/nap-now")
def nap_now_api():
    """The one-tap nap button: the maths picks the best nap time today."""
    data = db.load_user_data()
    now = web.local_now()
    status, plan = nap_now.choose_nap(data, now)
    return nap_now.plan_payload(status, plan)


@router.post("/api/me/nap-now/reason")
def nap_reason_api():
    """A short explanation of the same choice (Gemini, or Doze's own words)."""
    data = db.load_user_data()
    now = web.local_now()
    model = PersonModel(data, now)
    status, plan = nap_now.choose_nap(data, now, model=model)
    return nap_now.explain(data, now, status, plan, model=model)
