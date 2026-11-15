"""Local calendar display, read-only Google OAuth and explicit snapshot sync."""

from __future__ import annotations

import json
import secrets
from urllib.parse import urlsplit, urlunsplit
from datetime import date, datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app import web
from app.services import db
from app.services.calendar_ingest import CalendarImportError, import_google_events
from app.services.demo_calendar import demo_snapshot
from app.services.google_auth import GoogleConfigurationError, create_flow, fetch_calendar_events
from app.web import templates
from app.time_utils import instant

router = APIRouter()
OAUTH_MAX_AGE_SECONDS = 600


def _sync_calendar(reference: str) -> None:
    stored = db.load_google_connection(reference)
    if stored is None:
        raise HTTPException(status_code=401, detail="Connect Google Calendar before syncing.")
    metadata = db.load_calendar_metadata()
    try:
        fetched = fetch_calendar_events(json.loads(stored))
        events, display = import_google_events(fetched.events)
        db.save_google_connection(reference, fetched.credentials_json)
        db.replace_calendar_events(events, metadata={
            "synced_at": datetime.now(timezone.utc).isoformat(),
            "display_events": display,
            "sync_error": None,
        })
    except Exception as exc:
        # Provider exceptions may contain credentials or request URLs.
        message = str(exc) if isinstance(exc, CalendarImportError) else (
            "Google Calendar could not refresh. The last saved calendar was kept. "
            "Try syncing again or reconnect your account."
        )
        db.save_calendar_metadata({**metadata, "sync_error": message})
        raise HTTPException(status_code=502, detail=message) from None


def _canonical_login_redirect(request: Request, redirect_uri: str):
    """Keep the pending cookie on the registered callback host.

    A redirect is only allowed between exact loopback aliases on the same
    scheme and port. Other mismatches require correcting local configuration.
    """
    try:
        callback = urlsplit(redirect_uri)
        current = urlsplit(str(request.url))
        if (
            callback.scheme not in {"http", "https"}
            or not callback.hostname
            or callback.username is not None
            or callback.password is not None
            or callback.query
            or callback.fragment
            or callback.path != "/auth/callback"
        ):
            raise ValueError("invalid callback origin")
        callback_port = callback.port if callback.port is not None else (443 if callback.scheme == "https" else 80)
        current_port = current.port if current.port is not None else (443 if current.scheme == "https" else 80)
        if callback_port == 0:
            raise ValueError("callback port must identify a listening service")
    except (TypeError, ValueError, AttributeError):
        raise HTTPException(status_code=503, detail="GOOGLE_REDIRECT_URI must be a valid HTTP(S) URL ending in /auth/callback.") from None
    callback_origin = (callback.scheme, callback.hostname, callback_port)
    current_origin = (current.scheme, current.hostname, current_port)
    if callback_origin == current_origin:
        return None
    loopback = {"localhost", "127.0.0.1", "::1"}
    if (
        callback.hostname in loopback and current.hostname in loopback
        and callback.scheme == current.scheme and callback_port == current_port
    ):
        return RedirectResponse(urlunsplit((callback.scheme, callback.netloc, "/auth/google", "", "")), status_code=303)
    raise HTTPException(
        status_code=503,
        detail="Open Doze using the same scheme, hostname and port as GOOGLE_REDIRECT_URI before connecting Google Calendar.",
    )


@router.get("/auth/google")
def google_login(request: Request):
    verifier = secrets.token_urlsafe(64)
    try:
        flow = create_flow(code_verifier=verifier)
    except GoogleConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    canonical = _canonical_login_redirect(request, flow.redirect_uri)
    if canonical is not None:
        return canonical
    old_pending = request.session.pop("oauth_pending_ref", None)
    if old_pending:
        db.pop_oauth_pending(old_pending)
    authorization_url, state = flow.authorization_url(access_type="offline", prompt="consent")
    reference = secrets.token_urlsafe(32)
    db.save_oauth_pending(reference, {
        "state": state, "code_verifier": verifier,
        "created_at": datetime.now(timezone.utc).timestamp(),
    })
    request.session["oauth_pending_ref"] = reference
    return RedirectResponse(authorization_url, status_code=303)


@router.get("/auth/callback")
def google_callback(request: Request):
    reference = request.session.pop("oauth_pending_ref", None)
    pending = db.pop_oauth_pending(reference) if reference else None
    returned_state = request.query_params.get("state", "")
    if (
        not pending
        or not returned_state
        or not secrets.compare_digest(pending.get("state", ""), returned_state)
        or not 0 <= datetime.now(timezone.utc).timestamp() - pending.get("created_at", 0) <= OAUTH_MAX_AGE_SECONDS
    ):
        raise HTTPException(status_code=400, detail="Invalid or expired Google login. Connect again.")
    if request.query_params.get("error"):
        raise HTTPException(status_code=400, detail="Google Calendar access was not granted. Connect again when ready.")
    code = request.query_params.get("code")
    if not code:
        raise HTTPException(status_code=400, detail="Google did not return an authorization code.")
    try:
        flow = create_flow(state=pending["state"], code_verifier=pending["code_verifier"])
        # State was verified above. Exchanging only the code also supports the
        # registered localhost HTTP callback without disabling OAuth transport checks.
        flow.fetch_token(code=code, timeout=15)
        credentials_json = flow.credentials.to_json()
    except GoogleConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    except Exception:
        raise HTTPException(status_code=502, detail="Google login could not be completed. Please connect again.") from None
    previous = request.session.pop("google_connection_ref", None)
    if previous:
        db.delete_google_connection(previous)
    connection = secrets.token_urlsafe(32)
    db.save_google_connection(connection, credentials_json)
    request.session["google_connection_ref"] = connection
    try:
        _sync_calendar(connection)
    except HTTPException:
        # The calendar page explains a failed initial sync without exposing tokens.
        pass
    return RedirectResponse("/calendar", status_code=303)


@router.post("/calendar/sync")
def sync_calendar(request: Request):
    reference = request.session.get("google_connection_ref")
    if not reference:
        raise HTTPException(status_code=401, detail="Connect Google Calendar before syncing.")
    _sync_calendar(reference)
    return RedirectResponse("/calendar", status_code=303)


@router.post("/calendar/disconnect")
def disconnect_calendar(request: Request):
    reference = request.session.pop("google_connection_ref", None)
    if reference:
        db.delete_google_connection(reference)
        db.replace_calendar_events([], metadata={})
    return RedirectResponse("/calendar", status_code=303)


@router.post("/calendar/demo")
def load_demo_calendar():
    """Replace this account's saved events with a fake calendar dated from today."""
    events, display = demo_snapshot(web.local_now())
    db.replace_calendar_events(events, metadata={
        "synced_at": datetime.now(timezone.utc).isoformat(),
        "display_events": display, "sync_error": None, "demo": True,
    })
    return RedirectResponse("/calendar", status_code=303)


@router.post("/calendar/demo/clear")
def clear_demo_calendar():
    if db.load_calendar_metadata().get("demo"):
        db.replace_calendar_events([], metadata={})
    return RedirectResponse("/calendar", status_code=303)


@router.get("/calendar", response_class=HTMLResponse)
def calendar(request: Request):
    reference = request.session.get("google_connection_ref")
    connected = bool(reference and db.load_google_connection(reference))
    metadata = db.load_calendar_metadata()
    now = web.local_now()
    upcoming, recent = [], []
    for event in metadata.get("display_events", []):
        # All-day end dates are exclusive; timed entries preserve their actual
        # offset-bearing instants. An ongoing event remains in the upcoming list.
        if event.get("all_day"):
            still_relevant = date.fromisoformat(event["end"]) > now.date()
        else:
            still_relevant = instant(datetime.fromisoformat(event["end"])) > instant(now)
        (upcoming if still_relevant else recent).append(event)
    upcoming.sort(key=lambda event: instant(datetime.fromisoformat(event["start"])))
    recent.sort(key=lambda event: instant(datetime.fromisoformat(event["start"])), reverse=True)
    return templates.TemplateResponse(
        request=request, name="calendar.html", context={
            "events": upcoming,
            "recent_events": recent,
            "connected": connected,
            "synced_at": metadata.get("synced_at"),
            "sync_error": metadata.get("sync_error"),
            "demo": bool(metadata.get("demo")),
        },
    )
