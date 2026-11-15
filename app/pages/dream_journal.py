
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from app import web
from app.services import db

router = APIRouter()


class DreamCreate(BaseModel):
    title: str = Field(min_length=1, max_length=150)
    content: str = Field(min_length=1, max_length=10000)


def current_user_id(request: Request):
    account = getattr(request.state, "account", None)

    if account is None:
        raise HTTPException(
            status_code=401,
            detail="Login required"
        )

    return account["id"]


@router.get("/dream-journal", response_class=HTMLResponse)
def dream_journal(request: Request):
    user_id = current_user_id(request)

    return web.templates.TemplateResponse(
        request=request,
        name="dream_journal.html",
        context={
            "dreams": db.get_dreams(user_id),
        },
    )


@router.post("/api/dreams")
def create_dream(request: Request, dream: DreamCreate):
    user_id = current_user_id(request)

    dream_id = db.save_dream(
        user_id,
        dream.title.strip(),
        dream.content.strip(),
    )

    return {"id": dream_id, "saved": True}
