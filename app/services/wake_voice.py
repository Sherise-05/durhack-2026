"""Spoken wake-up messages for the nap and sleep alarms, using ElevenLabs text to speech.

A random message from WAKE_MESSAGES is read by a random pre-made ElevenLabs voice
(pre-made voices work on every plan). The SDK reads ELEVENLABS_API_KEY from the
environment, which the app loads from .env; the key never appears in code.
"""

from __future__ import annotations

import logging
import random
import time

log = logging.getLogger(__name__)

WAKE_MESSAGES = [
    "Good morning, {name}! Time to wake up. Stretch, take a deep breath, and open the curtains.",
    "Rise and shine, {name}! Your rest is done. Drink some water before you check your phone.",
    "Hey {name}, wakey wakey! Sit up slowly and give yourself a minute before you rush off.",
    "Time to get up, {name}. You did the sleeping part, now go and own the day.",
    "Hello {name}! The alarm is ringing, and so is the rest of your day. Up you get!",
    "Wake up, {name}! A little daylight in the next half hour will help you feel sharp.",
    "Good news, {name}: you're rested. Bad news: the duvet has to let you go now.",
    "{name}, it's time! Feet on the floor, shoulders back, and let's go.",
]
NAP_MESSAGES = [
    "Nap's over, {name}! Give yourself a few minutes before anything important.",
    "Wakey wakey, {name}. That was your power nap. Splash some water on your face and carry on.",
    "Time to get up, {name}! You should feel the lift from that nap in a few minutes.",
    "Rise and recharge, {name}. Nap done. Have a quick stretch before you dive back in.",
]

# Pre-made voices named in ElevenLabs' text-to-speech guide. Used when the key can't
# list voices (it lacks the voices_read permission), so the choice stays random.
FALLBACK_VOICES = [
    ("JBFqnCBsd6RMkjVDRZzb", "George"),
    ("EXAVITQu4vr4xnSDxMaL", "Sarah"),
    ("onwK4e9ZLuTAKqWW03F9", "Daniel"),
]
MODELS = ("eleven_v4", "eleven_flash_v2_5")  # best quality first, then the fast fallback
VOICE_CACHE_SECONDS = 3600
_voice_cache: tuple[float, list[tuple[str, str]]] | None = None


class WakeVoiceUnavailable(RuntimeError):
    pass


def pick_message(name: str, kind: str = "sleep") -> str:
    options = NAP_MESSAGES if kind == "nap" else WAKE_MESSAGES
    clean = (name or "").strip() or "there"
    return random.choice(options).format(name=clean)


def _client():
    from elevenlabs import ElevenLabs  # imported lazily so the app starts without the SDK

    return ElevenLabs()


def _premade_voices(client) -> list[tuple[str, str]]:
    """(voice_id, name) for the account's pre-made voices, cached for an hour."""
    global _voice_cache
    if _voice_cache and time.monotonic() - _voice_cache[0] < VOICE_CACHE_SECONDS:
        return _voice_cache[1]
    try:
        voices = [(v.voice_id, v.name) for v in client.voices.get_all().voices if v.category == "premade"]
    except Exception as exc:  # e.g. a key without voices_read: use the guide's pre-made voices
        log.warning("Couldn't list ElevenLabs voices (%s); choosing from the default pre-made voices.", type(exc).__name__)
        voices = []
    voices = voices or list(FALLBACK_VOICES)
    _voice_cache = (time.monotonic(), voices)
    return voices


def speak(text: str) -> tuple[bytes, str]:
    """Return (mp3 bytes, voice name) for ``text`` in a randomly chosen pre-made voice."""
    client = _client()
    voice_id, voice_name = random.choice(_premade_voices(client))
    last_error: Exception | None = None
    for model in MODELS:
        try:
            audio = client.text_to_speech.convert(
                voice_id=voice_id, text=text, model_id=model, output_format="mp3_44100_128",
            )
            data = b"".join(audio)
            if data:
                return data, voice_name
        except Exception as exc:
            last_error = exc
            log.warning("ElevenLabs %s failed (%s); trying the next model.", model, type(exc).__name__)
    raise WakeVoiceUnavailable(f"ElevenLabs couldn't generate speech ({type(last_error).__name__ if last_error else 'empty audio'}).")


# --------------------------------------------------------------------------- day summary

from datetime import time as _time

# Our choice: "save your hardest work for then" should point at the working day,
# even though the model often predicts a second, early-evening peak.
WORKDAY = (_time(8, 0), _time(18, 0))

def spoken_time(t) -> str:
    """'9 a.m.', '3:30 p.m.': easy for a voice to read and for a person to hear."""
    hour = t.hour % 12 or 12
    suffix = "a.m." if t.hour < 12 else "p.m."
    return f"{hour} {suffix}" if t.minute == 0 else f"{hour}:{t.minute:02d} {suffix}"


def _list(items: list[str]) -> str:
    if len(items) <= 2:
        return " and ".join(items)
    return ", ".join(items[:-1]) + ", and " + items[-1]


def _sentence(text: str) -> str:
    """End with exactly one full stop, even after 'a.m.' or 'p.m.'."""
    text = text.rstrip()
    return text if text.endswith((".", "!", "?")) else text + "."


def day_summary(data, now, kind: str = "sleep", model=None) -> str:
    """A short spoken plan for the day you're waking into.

    After a night's sleep: the whole day (the day of the planned alarm). After a
    nap: what's left of today. Built from the calendar and the sleep model only.
    """
    from datetime import datetime, time as clock_time, timedelta

    from app.schemas import EventCategory, Mode
    from app.services import planner
    from app.services.modes import effective_mode
    from app.services.sleep_model import PersonModel
    from app.time_utils import instant

    model = model or PersonModel(data, now)
    night = planner.recommend_bedtime(data, now, model=model)
    if kind == "nap":
        day, after = now.date(), now
    else:
        day, after = night.alarm.date(), night.alarm
    events = sorted((e for e in data.events if e.start.date() == day and e.category != EventCategory.REST
                     and instant(e.end) > instant(after)), key=lambda e: instant(e.start))
    parts = []
    if events:
        named = [f"{e.title} at {spoken_time(e.start)}" for e in events[:3]]
        extra = f", plus {len(events) - 3} more" if len(events) > 3 else ""
        count = "1 thing" if len(events) == 1 else f"{len(events)} things"
        lead = "Here's the rest of your day." if kind == "nap" else "Here's your day."
        parts.append(_sentence(f"{lead} You have {count} on: {_list(named)}{extra}"))
    else:
        parts.append("Nothing else in your calendar today." if kind == "nap" else "Your calendar is clear today.")
    exam = next((e for e in events if e.category == EventCategory.EXAM), None)
    mode, _ = effective_mode(data, now)
    if exam is not None:
        parts.append(f"Good luck in your {exam.title}!")
    if kind != "nap":
        try:
            curve = planner.what_if_bedtime(data, now, night.bedtime, night.alarm, model=model).curve
        except ValueError:
            curve = []
        awake = [p for p in curve if p.alertness is not None and p.at.date() == day
                 and instant(p.at) >= instant(night.alarm + timedelta(hours=1))
                 and WORKDAY[0] <= p.at.time() <= WORKDAY[1]]
        if awake:
            peak = max(awake, key=lambda p: p.alertness)
            afternoon = [p for p in awake if clock_time(12, 0) <= p.at.time() <= clock_time(17, 0)]
            dip = min(afternoon, key=lambda p: p.alertness) if afternoon else None
            line = f"You'll be sharpest around {spoken_time(peak.at)}, so save your hardest work for then"
            if dip is not None and dip.alertness < peak.alertness - 5:
                line += f", and expect a dip around {spoken_time(dip.at)}"
            parts.append(_sentence(line))
    if night.feasible and not night.in_progress:
        parts.append(_sentence(f"Aim to start winding down at {spoken_time(night.wind_down)}"
                               + (" tonight" if kind == "nap" else "")))
    if mode == Mode.TRAVEL:
        parts.append("Remember your jet-lag plan.")
    return " ".join(parts)
