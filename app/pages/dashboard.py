from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from app.services.tiger_data import fetch_calendar_events, fetch_sleep_history

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    sleep_data = await fetch_sleep_history()
    calendar_events = await fetch_calendar_events()
    return templates.TemplateResponse("dashboard.html", {"request": request, "sleep_data": sleep_data, "calendar_events": calendar_events})
