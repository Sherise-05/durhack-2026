
from datetime import datetime
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.db import get_connection
from app.services.sleep_analysis import analyse_sleep
from app.services.sleep_coach import generate_sleep_advice

router = APIRouter(prefix="/api")

ALLOWED_ACTIONS = {
    "set_bedtime_goal",
    "set_wind_down_reminder",
    "set_sleep_goal"
}


class Preferences(BaseModel):
    chronotype: str = "late"
    sleep_goal: float = Field(default=8, ge=6, le=10)
    sleep_challenge: str = "late"
    schedule_flexibility: str = "some"


class SleepAction(BaseModel):
    title: str
    action_type: str
    action_value: str


def load_preferences(persona_id):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM sleep_preferences WHERE persona_id = ?",
            (persona_id,)
        ).fetchone()

    return dict(row) if row else Preferences().model_dump()


@router.get("/sleep-insights")
def sleep_insights(persona_id: str = "maya"):
    try:
        prefs = load_preferences(persona_id)
        return analyse_sleep(
            persona_id,
            prefs["sleep_goal"]
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.get("/sleep-preferences")
def get_preferences(persona_id: str = "maya"):
    return load_preferences(persona_id)


@router.post("/sleep-preferences")
def save_preferences(
    preferences: Preferences,
    persona_id: str = "maya"
):
    if preferences.chronotype not in ("early", "late"):
        raise HTTPException(400, "Invalid chronotype")

    if preferences.sleep_challenge not in (
        "late", "falling", "waking", "schedule"
    ):
        raise HTTPException(400, "Invalid sleep challenge")

    if preferences.schedule_flexibility not in (
        "very", "some", "fixed"
    ):
        raise HTTPException(400, "Invalid schedule flexibility")

    with get_connection() as conn:
        conn.execute("""
            INSERT INTO sleep_preferences
            (persona_id, chronotype, sleep_goal,
             sleep_challenge, schedule_flexibility)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(persona_id) DO UPDATE SET
                chronotype=excluded.chronotype,
                sleep_goal=excluded.sleep_goal,
                sleep_challenge=excluded.sleep_challenge,
                schedule_flexibility=excluded.schedule_flexibility
        """, (
            persona_id,
            preferences.chronotype,
            preferences.sleep_goal,
            preferences.sleep_challenge,
            preferences.schedule_flexibility
        ))

    return {"saved": True}


@router.post("/sleep-coach")
def sleep_coach(persona_id: str = "maya"):
    try:
        prefs = load_preferences(persona_id)
        return generate_sleep_advice(persona_id, prefs)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        print("Gemini error:", repr(exc))
        raise HTTPException(502, "Could not generate advice")


@router.post("/sleep-actions")
def apply_sleep_action(
    action: SleepAction,
    persona_id: str = "maya"
):
    if action.action_type not in ALLOWED_ACTIONS:
        raise HTTPException(400, "Invalid action type")

    if action.action_type == "set_sleep_goal":
        try:
            goal = float(action.action_value)
        except ValueError:
            raise HTTPException(400, "Invalid sleep goal")

        if not 6 <= goal <= 10:
            raise HTTPException(400, "Sleep goal must be 6-10 hours")
    else:
        try:
            datetime.strptime(action.action_value, "%H:%M")
        except ValueError:
            raise HTTPException(400, "Time must be HH:MM")

    with get_connection() as conn:
        conn.execute("""
            INSERT INTO sleep_actions
            (persona_id, action_type, action_value, title)
            VALUES (?, ?, ?, ?)
        """, (
            persona_id,
            action.action_type,
            action.action_value,
            action.title
        ))

        if action.action_type == "set_sleep_goal":
            conn.execute("""
                INSERT INTO sleep_preferences (persona_id, sleep_goal)
                VALUES (?, ?)
                ON CONFLICT(persona_id) DO UPDATE SET
                    sleep_goal=excluded.sleep_goal
            """, (persona_id, goal))

    return {
        "applied": True,
        "action": action.model_dump()
    }
