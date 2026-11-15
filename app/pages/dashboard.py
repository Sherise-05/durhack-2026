
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):

    # Temporary data until SQLite is connected
    sleep_data = []

    # Temporary data until Google Calendar is connected
    calendar_events = []

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "sleep_data": sleep_data,
            "calendar_events": calendar_events
        }
    )
