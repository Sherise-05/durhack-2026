
import json
from pathlib import Path
from datetime import datetime, timedelta
from statistics import mean

DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "personas.json"


def load_persona(persona_id="maya"):
    with open(DATA_PATH, encoding="utf-8") as f:
        personas = json.load(f)

    if persona_id not in personas or not isinstance(personas[persona_id], dict):
        raise ValueError(f"Unknown persona: {persona_id}")

    return personas[persona_id]


def sleep_duration_hours(bed, wake, efficiency):
    bed_time = datetime.strptime(bed, "%H:%M")
    wake_time = datetime.strptime(wake, "%H:%M")

    if wake_time <= bed_time:
        wake_time += timedelta(days=1)

    hours_in_bed = (wake_time - bed_time).total_seconds() / 3600
    return hours_in_bed * efficiency


def analyse_sleep(persona_id="maya", sleep_goal=8.0):
    persona = load_persona(persona_id)
    nights = persona["nights"]

    if not nights:
        raise ValueError("No sleep history available")

    records = []

    for night in nights:
        duration = sleep_duration_hours(
            night["bed"],
            night["wake"],
            night["efficiency"]
        )

        records.append({
            "night": night["night"],
            "bed": night["bed"],
            "wake": night["wake"],
            "duration_hours": round(duration, 2),
            "efficiency": night["efficiency"],
            "latency_min": night["latency_min"],
            "hrv_ms": night["hrv_ms"],
            "deep_pct": night["deep_pct"],
            "rem_pct": night["rem_pct"],
            "awakenings": night["awakenings"],
            "resting_hr": night["resting_hr"],
            "rating": night["rating"]
        })

    records.sort(key=lambda r: r["night"])

    recent = records[-7:]
    average_sleep = mean(r["duration_hours"] for r in recent)
    average_efficiency = mean(r["efficiency"] for r in recent)
    average_latency = mean(r["latency_min"] for r in recent)

    latest = records[-1]
    short_nights = sum(
        r["duration_hours"] < sleep_goal for r in recent
    )

    return {
        "persona_id": persona_id,
        "name": persona["profile"]["name"],
        "chronotype": persona["profile"]["chronotype"],
        "sleep_goal_hours": sleep_goal,
        "average_sleep_hours": round(average_sleep, 2),
        "average_efficiency": round(average_efficiency, 2),
        "average_latency_minutes": round(average_latency, 1),
        "average_goal_gap_hours": round(
            max(0, sleep_goal - average_sleep), 2
        ),
        "short_nights_last_7": short_nights,
        "latest_night": latest,
        "recent_nights": recent
    }
