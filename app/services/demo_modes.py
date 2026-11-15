"""Demo accounts that show each mode with fake data, without touching a real account.

Each mode uses one of the demo personas (data/personas.json, dates relative to
today). Pressing a demo button signs you in to that persona's own demo account,
freshly reloaded; your own account and its data are never changed.
"""

from __future__ import annotations

from datetime import datetime, timezone
import secrets

from app.services import accounts, db, personas
from app.time_utils import instant

DEMOS = {
    "normal": {"persona": "june", "label": "Normal", "icon": "☀️"},
    "exam": {"persona": "maya", "label": "Exam", "icon": "📚"},
    "holiday": {"persona": "leo", "label": "Holiday", "icon": "🏖️"},
    "travel": {"persona": "priya", "label": "Travel", "icon": "✈️"},
}
DEMO_EMAIL_DOMAIN = "demo.doze.invalid"  # .invalid can never be a real address


def demo_email(mode: str) -> str:
    return f"{DEMOS[mode]['persona']}@{DEMO_EMAIL_DOMAIN}"


def is_demo_account(account: dict | None) -> bool:
    return bool(account) and account.get("email", "").endswith("@" + DEMO_EMAIL_DOMAIN)


def load_demo(mode: str, now: datetime) -> dict:
    """Create (or reuse) the demo account for ``mode`` and reload its fake data."""
    if mode not in DEMOS:
        raise ValueError("Unknown demo mode.")
    data = personas.load_persona(DEMOS[mode]["persona"], now.date())
    account = db.find_account_by_email(demo_email(mode))
    if account is None:
        # Nobody can sign in with this password: it is random and never shown.
        account = db.create_account(demo_email(mode), accounts.hash_password(secrets.token_urlsafe(24)),
                                    data.profile.name)
    account = db.get_account(account["id"])
    with db.account_scope(account["id"]):
        for record in db.list_sleep_records():
            db.delete_sleep_session(record["source_id"])
        for index, session in enumerate(data.sessions):
            if instant(session.end) <= instant(now):  # completed sleep only
                db.upsert_sleep_session(f"demo-{index:03d}", session, now=now)
        display = [{
            "id": f"demo-{i}", "title": e.title, "start": e.model_dump(mode="json")["start"],
            "end": e.model_dump(mode="json")["end"], "all_day": False, "blocks_sleep": True, "category": e.category.value,
        } for i, e in enumerate(data.events)]
        db.replace_calendar_events(data.events, metadata={
            "synced_at": datetime.now(timezone.utc).isoformat(), "display_events": display,
            "sync_error": None, "demo": True,
        })
        if account["onboarding_complete"]:
            db.save_profile(data.profile)
        else:
            db.complete_onboarding(account["id"], data.profile)
    return db.get_account(account["id"])
