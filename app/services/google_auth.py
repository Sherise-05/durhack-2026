
import os
from dotenv import load_dotenv
from google_auth_oauthlib.flow import Flow

from datetime import datetime, timedelta, timezone
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request as GoogleRequest
from googleapiclient.discovery import build


load_dotenv()

# Google OAuth configuration
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
GOOGLE_AUTH_URI = os.getenv(
    "GOOGLE_AUTH_URI",
    "https://accounts.google.com/o/oauth2/auth"
)
GOOGLE_TOKEN_URI = os.getenv(
    "GOOGLE_TOKEN_URI",
    "https://oauth2.googleapis.com/token"
)
GOOGLE_AUTH_PROVIDER_CERT_URL = os.getenv(
    "GOOGLE_AUTH_PROVIDER_CERT_URL",
    "https://www.googleapis.com/oauth2/v1/certs"
)
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI")

SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly"
]


# OAuth configuration
def get_google_oauth_config():
    return {
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "auth_uri": GOOGLE_AUTH_URI,
        "token_uri": GOOGLE_TOKEN_URI,
        "auth_provider_x509_cert_url": GOOGLE_AUTH_PROVIDER_CERT_URL,
        "redirect_uris": [GOOGLE_REDIRECT_URI],
    }


# Create Google login flow
def create_flow():
    return Flow.from_client_config(
        {"web": get_google_oauth_config()},
        scopes=SCOPES,
        redirect_uri=GOOGLE_REDIRECT_URI
    )


# Fetch actual Google Calendar events
def fetch_calendar_events(credentials_data):
    credentials = Credentials.from_authorized_user_info(
        credentials_data,
        scopes=SCOPES
    )

    service = build(
        "calendar",
        "v3",
        credentials=credentials,
        cache_discovery=False
    )

    now = datetime.now(timezone.utc).isoformat()

    result = service.events().list(
        calendarId="primary",
        timeMin=now,
        maxResults=50,
        singleEvents=True,
        orderBy="startTime"
    ).execute()

    events = []

    for event in result.get("items", []):
        events.append({
            "title": event.get("summary", "Untitled"),
            "start": event["start"].get(
                "dateTime", event["start"].get("date")
            ),
            "end": event["end"].get(
                "dateTime", event["end"].get("date")
            )
        })

    return events


def fetch_calendar_events(credentials_data):
    credentials = Credentials.from_authorized_user_info(
        credentials_data,
        scopes=SCOPES
    )

    if credentials.expired and credentials.refresh_token:
        credentials.refresh(GoogleRequest())

    service = build(
        "calendar",
        "v3",
        credentials=credentials,
        cache_discovery=False
    )

    now = datetime.now(timezone.utc)

    # Previous 7 days and next 7 days
    start = now - timedelta(days=7)
    end = now + timedelta(days=7)

    events = []
    page_token = None

    while True:
        result = service.events().list(
            calendarId="primary",
            timeMin=start.isoformat(),
            timeMax=end.isoformat(),
            singleEvents=True,
            orderBy="startTime",
            maxResults=250,
            pageToken=page_token
        ).execute()

        events.extend(result.get("items", []))

        page_token = result.get("nextPageToken")
        if not page_token:
            break

    return events
