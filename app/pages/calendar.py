from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from app.services.tiger_data import fetch_calendar_events

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/calendar", response_class=HTMLResponse)
async def calendar(request: Request):
    events = await fetch_calendar_events()
    return templates.TemplateResponse("calendar.html", {"request": request, "events": events})
