"""Shared rendering paths and the server-controlled calculation clock."""

from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.templating import Jinja2Templates

from app.time_utils import add_elapsed, local_datetime


def _display_value(value):
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            try:
                value = time.fromisoformat(value)
            except ValueError:
                return None
    return local_datetime(value) if isinstance(value, datetime) else value


def clock(value) -> str:
    value = _display_value(value)
    return value.strftime("%H:%M") if isinstance(value, (datetime, time)) else "—"


def clock_ceil(value) -> str:
    value = _display_value(value)
    if isinstance(value, datetime) and (value.second or value.microsecond):
        value = add_elapsed(value.replace(second=0, microsecond=0), timedelta(minutes=1))
    return clock(value)


def datetime_label(value) -> str:
    value = _display_value(value)
    if isinstance(value, datetime):
        label = value.strftime("%a %d %b" + (" %Y" if value.year != local_now().year else "") + " · %H:%M")
        london = ZoneInfo("Europe/London")
        if value.replace(tzinfo=london, fold=0).utcoffset() != value.replace(tzinfo=london, fold=1).utcoffset():
            label += " " + value.replace(tzinfo=london).tzname()
        return label
    return value.strftime("%a %d %b") if isinstance(value, date) else "—"


def date_label(value) -> str:
    value = _display_value(value)
    return value.strftime("%a %d %b") if isinstance(value, date) else "—"


def duration_h(value) -> str:
    if value is None:
        return "—"
    minutes = round(float(value) * 60)
    return f"{minutes // 60}h {minutes % 60:02d}m"


def nap_reason(reason: str, plan) -> str:
    if plan.alarm is not None:
        reason = reason.replace(f"Alarm {clock(plan.alarm)}:", f"Alarm {clock_ceil(plan.alarm)}:")
    return reason


def _page_context(request) -> dict:
    from app.services import db
    from app.services.modes import MODES, effective_mode

    account = getattr(request.state, "account", None)
    public_context = {
        "account": account,
        "authenticated": account is not None,
        "profile_complete": bool(account and account["onboarding_complete"]),
        "calendar_status": {},
    }
    if not public_context["profile_complete"]:
        return public_context
    data = db.load_user_data()
    mode, reason = effective_mode(data, local_now())
    return {
        **public_context,
        "calendar_status": db.load_calendar_metadata(),
        "site_profile": data.profile,
        "site_mode": mode,
        "mode_label": MODES[mode].label,
        "mode_reason": reason,
        "mode_automatic": mode != data.profile.mode,
    }


templates = Jinja2Templates(
    directory=Path(__file__).resolve().parent / "templates",
    context_processors=[_page_context],
)
templates.env.filters.update(clock=clock, clock_ceil=clock_ceil, datetime_label=datetime_label,
                             date_label=date_label, duration_h=duration_h, nap_reason=nap_reason)


def local_now() -> datetime:
    return local_datetime(datetime.now(timezone.utc))
