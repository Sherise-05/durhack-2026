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
