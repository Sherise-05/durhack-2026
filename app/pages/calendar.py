
from fastapi.responses import HTMLResponse
from fastapi import APIRouter, Request
import json
import secrets

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
from datetime import datetime, timezone

from app.services.google_auth import (
    create_flow,
    fetch_calendar_events
)

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


# Step 1: Redirect user to Google login
@router.get("/auth/google")
def google_login(request: Request):
    flow = create_flow()

    authorization_url, state = flow.authorization_url(
        access_type="offline",
        prompt="consent"
    )

    request.session["oauth_state"] = state

    return RedirectResponse(authorization_url)


@router.get("/auth/callback")
def google_callback(request: Request):
    saved_state = request.session.get("oauth_state")
    returned_state = request.query_params.get("state")

    print("Saved state:", saved_state)
    print("Returned state:", returned_state)

    if (
        not saved_state
        or not returned_state
        or not secrets.compare_digest(saved_state, returned_state)
    ):
        raise HTTPException(
            status_code=400,
            detail="Invalid OAuth state"
        )

    flow = create_flow()

    # Use the state originally generated during login
    flow = create_flow()
    flow.fetch_token(
        authorization_response=str(request.url)
    )

    request.session.pop("oauth_state", None)
    request.session["google_credentials"] = flow.credentials.to_json()

    return RedirectResponse("/calendar", status_code=303)


# @router.get("/calendar", response_class=HTMLResponse)
# def calendar(request: Request):
#     stored = request.session.get("google_credentials")

#     if not stored:
#         return RedirectResponse("/auth/google")

#     # Fetch Google Calendar events
#     events = fetch_calendar_events(json.loads(stored))

#     # Save original Google data to JSON
#     data_dir = Path("data")
#     data_dir.mkdir(exist_ok=True)

#     output = {
#         "fetched_at": datetime.now(timezone.utc).isoformat(),
#         "events": events
#     }

#     with open(data_dir / "google_calendar.json", "w") as f:
#         json.dump(output, f, indent=2)

#     # Keep frontend compatible with existing title/start/end format
#     display_events = [
#         {
#             "title": event.get("summary", "Untitled"),
#             "start": event["start"].get(
#                 "dateTime", event["start"].get("date")
#             ),
#             "end": event["end"].get(
#                 "dateTime", event["end"].get("date")
#             )
#         }
#         for event in events
#         if event.get("start") and event.get("end")
#     ]

#     return templates.TemplateResponse(
#         request=request,
#         name="calendar.html",
#         context={"events": display_events}
#     )


router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

MOCK_CALENDAR_PATH = Path("app/data/mock_calendar.json")


@router.get("/calendar", response_class=HTMLResponse)
def calendar(request: Request):
    # Load mock calendar events
    with open(MOCK_CALENDAR_PATH, "r") as f:
        data = json.load(f)

    # Convert Google Calendar format to frontend format
    events = [
        {
            "title": event.get("summary", "Untitled"),
            "start": event["start"].get("dateTime", event["start"].get("date")),
            "end": event["end"].get("dateTime", event["end"].get("date"))
        }
        for event in data["items"]
        if event.get("start") and event.get("end")
    ]

    return templates.TemplateResponse(
        request=request,
        name="calendar.html",
        context={"events": events}
    )
