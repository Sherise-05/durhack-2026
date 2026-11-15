"""Dream journal: speak or type a dream; Doze titles it, reads its mood and draws a postcard."""

from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from app import web
from app.services import brain_dump, db, dream_insights

router = APIRouter()

RATING_WORDS = {1: "exhausted", 2: "groggy", 3: "OK", 4: "rested", 5: "great"}


class DreamCreate(BaseModel):
    title: str = Field("", max_length=150)  # blank: Doze suggests one
    content: str = Field(min_length=1, max_length=10000)
    kind: Literal["dream", "day", "dump"] = "dream"


def current_user_id(request: Request):
    account = getattr(request.state, "account", None)
    if account is None:
        raise HTTPException(status_code=401, detail="Login required")
    return account["id"]


def _card(dream: dict, sessions) -> dict:
    if dream.get("kind") == "dump":
        return {**dream, "when": dream_insights.created_local(dream.get("created_at"))}
    details = dream.get("details") or dream_insights.analyse(dream["content"], dream.get("kind") or "dream")
    when = dream_insights.created_local(dream.get("created_at"))
    night = dream_insights.night_before(sessions, when)
    return {
        **dream,
        "details": details,
        "when": when,
        "layout": dream_insights.postcard_layout(dream["id"]),
        "night": None if night is None else {
            "hours": night.hours_asleep,
            "felt": RATING_WORDS.get(night.rating),
        },
    }


@router.get("/dream-journal", response_class=HTMLResponse)
def dream_journal(request: Request):
    user_id = current_user_id(request)
    sessions = db.load_user_data().sessions
    dreams = [_card(dream, sessions) for dream in db.get_dreams(user_id)]
    moods: dict[str, int] = {}
    for dream in dreams:
        if dream.get("kind") != "dump":
            moods[dream["details"]["mood"]] = moods.get(dream["details"]["mood"], 0) + 1
    return web.templates.TemplateResponse(
        request=request,
        name="dream_journal.html",
        context={
            "dreams": [d for d in dreams if (d.get("kind") or "dream") == "dream"],
            "days": [d for d in dreams if d.get("kind") == "day"],
            "dumps": [d for d in dreams if d.get("kind") == "dump"],
            "start_kind": request.query_params.get("kind") if request.query_params.get("kind") in ("dream", "day", "dump") else "dream",
            "moods": sorted(moods.items(), key=lambda kv: -kv[1]),
        },
    )


@router.post("/api/dreams")
def create_dream(request: Request, dream: DreamCreate):
    user_id = current_user_id(request)
    content = dream.content.strip()
    if not content:
        raise HTTPException(status_code=422, detail="Tell Doze a little about your dream first.")
    if dream.kind == "dump":
        details = brain_dump.analyse(db.load_user_data(), web.local_now(), content)
    else:
        details = dream_insights.analyse(content, dream.kind)
    title = dream.title.strip() or details["title"]
    dream_id = db.save_dream(user_id, title, content, details, kind=dream.kind)
    return {"id": dream_id, "saved": True, "title": title, "kind": dream.kind, "details": details}


@router.delete("/api/dreams/{dream_id}")
def delete_dream(request: Request, dream_id: int):
    user_id = current_user_id(request)
    if not db.delete_dream(user_id, dream_id):
        raise HTTPException(status_code=404, detail="Dream not found")
    return {"deleted": True}
