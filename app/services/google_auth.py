"""Google Calendar read-only OAuth helpers.

Credentials stay in server-side storage; callers only put a random reference
in the browser session. Configuration is read at call time so an unconfigured
Google account never prevents the local application from starting.
"""

from __future__ import annotations

import os
from functools import partial
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]


class GoogleConfigurationError(ValueError):
    """The local app does not yet have Google OAuth client settings."""


@dataclass
class CalendarFetchResult:
    events: list[dict]
    credentials_json: str


def get_google_oauth_config() -> dict:
    required = ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REDIRECT_URI")
    if any(not os.getenv(key, "").strip() for key in required):
        raise GoogleConfigurationError(
            "Google Calendar is not configured. Set GOOGLE_CLIENT_ID, "
            "GOOGLE_CLIENT_SECRET and GOOGLE_REDIRECT_URI in .env."
        )
    return {
        "client_id": os.environ["GOOGLE_CLIENT_ID"],
        "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
        "redirect_uris": [os.environ["GOOGLE_REDIRECT_URI"]],
    }


def create_flow(*, state: str | None = None, code_verifier: str | None = None):
    from google_auth_oauthlib.flow import Flow

    config = get_google_oauth_config()
    return Flow.from_client_config(
        {"web": config},
        scopes=SCOPES,
        redirect_uri=config["redirect_uris"][0],
        state=state,
        code_verifier=code_verifier,
        autogenerate_code_verifier=False,
    )


def fetch_calendar_events(credentials_data: dict, *, now: datetime | None = None) -> CalendarFetchResult:
    """Fetch a complete bounded snapshot and return any refreshed credentials.

    Include a month ahead so the brain can see an exam within two weeks.
    Pagination is completed before callers replace their last saved snapshot.
    """
    import httplib2
    from google.auth.transport.requests import Request as GoogleRequest
    from google.oauth2.credentials import Credentials
    from google_auth_httplib2 import AuthorizedHttp
    from googleapiclient.discovery import build

    credentials = Credentials.from_authorized_user_info(credentials_data, scopes=SCOPES)
    if credentials.expired and credentials.refresh_token:
        credentials.refresh(partial(GoogleRequest(), timeout=15))

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("calendar sync requires an aware calculation time")
    current = current.astimezone(timezone.utc)
    transport = AuthorizedHttp(credentials, http=httplib2.Http(timeout=15))
    service = build("calendar", "v3", http=transport, cache_discovery=False)
    events: list[dict] = []
    page_token = None
    while True:
        result = service.events().list(
            calendarId="primary",
            timeMin=(current - timedelta(days=7)).isoformat(),
            timeMax=(current + timedelta(days=30)).isoformat(),
            singleEvents=True,
            orderBy="startTime",
            maxResults=250,
            pageToken=page_token,
        ).execute()
        events.extend(result.get("items", []))
        page_token = result.get("nextPageToken")
        if not page_token:
            break
    return CalendarFetchResult(events, credentials.to_json())
