from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/calendar", response_class=HTMLResponse)
async def calendar(request: Request):
    # Temporary mock calendar events
    events = [
        {
            "title": "University Lecture",
            "start": "2026-11-15T09:00:00",
            "end": "2026-11-15T11:00:00"
        },
        {
            "title": "Study Session",
            "start": "2026-11-15T14:00:00",
            "end": "2026-11-15T16:00:00"
        }
    ]

    return templates.TemplateResponse(
        request=request,
        name="calendar.html",
        context={"events": events}
    )
