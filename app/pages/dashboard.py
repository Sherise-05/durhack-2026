
"""A contextual Today page over the inputs shared by every planner."""

from datetime import datetime, timedelta

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app import web
from app.schemas import EventCategory
from app.services import db, planner
from app.services.nap_recc import recommend_nap
from app.services.sleep_model import PersonModel, nightly_stats
from app.time_utils import instant, local_isoformat

router = APIRouter()


def _dashboard_data():
    data = db.load_user_data()
    now = web.local_now()
    model = PersonModel(data, now)
    return data, model, planner.snapshot(data, now, model=model), now


def _recent_sleep(nights, now, need_h):
    """Calendar-day totals from completed primary nights; missing is not zero."""
    recorded = {}
    for night in nights:
        recorded[night.morning] = recorded.get(
            night.morning, 0) + night.hours_asleep
    return {
        "points": [
            {"label": day.strftime("%d %b"), "value": recorded.get(day)}
            for day in (now.date() - timedelta(days=offset) for offset in range(6, -1, -1))
        ],
        "reference": need_h,
    }


def _last_night_guess(profile, now):
    """Pre-fill the morning check-in with last night's usual routine.

    Only a suggestion for the form: nothing is saved until the user taps a rating.
    """
    today = now.date()
    wake = datetime.combine(today, profile.usual_wake)
    if instant(wake) > instant(now):  # up before the usual alarm: use now
        wake = now.replace(second=0, microsecond=0)
    bed_day = today if profile.usual_bedtime < profile.usual_wake else today - timedelta(days=1)
    bed = datetime.combine(bed_day, profile.usual_bedtime)
    if instant(bed) >= instant(wake):
        bed -= timedelta(days=1)
    return {"start": local_isoformat(bed), "end": local_isoformat(wake)}


def _next_step(data, now, model, snapshot, bedtime):
    if bedtime.in_progress:
        return {
            "kind": "in_progress", "title": "Your sleep is already in progress.",
            "body": "Review your existing wake-up plan rather than starting another sleep window.",
            "href": "/sleep-planner", "label": "View your wake-up plan", "nap": None,
        }
    if now.hour >= 20 or now.hour < 5:
        return {
            "kind": "bedtime", "title": "Make room for tonight’s sleep.",
            "body": "Your routine, recorded sleep and calendar shape your next bedtime and wake-up suggestion.",
            "href": "/sleep-planner", "label": "View tonight’s plan", "nap": None,
        }
    nap = recommend_nap(data, now, model=model)
    return {
        "kind": "nap" if nap.should_nap else "rest",
        "title": "A little recharge could fit your day." if nap.should_nap else "No nap is suggested right now.",
        "body": "This suggestion uses your recorded sleep and available time. Tell us how tired you feel to review it." if nap.should_nap else nap.headline,
        "href": "/nap-planner", "label": "Review nap options", "nap": nap,
    }


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    data, model, snapshot, now = _dashboard_data()
    bedtime = planner.recommend_bedtime(data, now, model=model)
    nights = nightly_stats(data.sessions, model.need_h, now)
    next_event = min(
        (event for event in data.events
         if event.category != EventCategory.REST and instant(event.end) > instant(now)),
        key=lambda event: instant(event.start), default=None,
    )
    greeting = "Good morning" if 5 <= now.hour < 12 else "Good afternoon" if 12 <= now.hour < 18 else "Good evening"
    return web.templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "profile": data.profile,
            "snapshot": snapshot,
            "informative_check_ins": model.personal.need.n,
            "greeting": greeting,
            "bedtime": bedtime,
            "next_action": _next_step(data, now, model, snapshot, bedtime),
            "next_event": next_event,
            "next_event_in_progress": bool(next_event and instant(next_event.start) <= instant(now)),
            "curve": [point.model_dump(mode="json") for point in planner.day_curve(data, now, model=model)] if nights else [],
            "recent_sleep_chart": _recent_sleep(nights, now, snapshot.sleep_need_h),
            "has_sleep_history": bool(data.sessions),
            "has_completed_history": bool(nights),
            "last_night_guess": None if snapshot.last_night or bedtime.in_progress else _last_night_guess(data.profile, now),
        },
    )


@router.get("/api/me/dashboard")
def dashboard_api():
    data, model, snapshot, now = _dashboard_data()
    return {
        "snapshot": snapshot.model_dump(mode="json"),
        "curve": [point.model_dump(mode="json") for point in planner.day_curve(data, now, model=model)],
        "has_sleep_history": bool(data.sessions),
    }
