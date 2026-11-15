"""Primary-night summaries shared by learning, readiness and mode planning.

Wearable fragments remain separate for physical simulation. An explicit
``episode_id`` groups primary-sleep fragments authoritatively. Legacy records
without IDs are grouped only within the same 14:00-to-14:00 sleep day, no more
than two hours apart and with an overall span of at most 24 elapsed hours.
These fallback boundaries are ingestion policy, not physiological thresholds.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from app.schemas import SleepSession, normalise_sessions
from app.time_utils import hours_between, instant

LEGACY_MAX_GAP_H = 2.0


def sleep_day(session: SleepSession) -> date:
    """Local sleep-day label used by the debt and legacy ingestion policies."""
    return (session.end - timedelta(hours=14)).date()


def _summary(fragments: list[SleepSession]) -> SleepSession:
    if len(fragments) == 1:
        return fragments[0]
    fragments = sorted(fragments, key=lambda s: instant(s.start))
    first, last = fragments[0], fragments[-1]
    elapsed = hours_between(first.start, last.end)
    if elapsed > 24:
        raise ValueError("a primary sleep episode must span at most 24 hours")
    actual = sum(s.hours_asleep for s in fragments)

    def latest(name: str):
        return next((getattr(s, name) for s in reversed(fragments) if getattr(s, name) is not None), None)

    def weighted_stage(name: str):
        known = [(getattr(s, name), s.hours_asleep) for s in fragments if getattr(s, name) is not None and s.hours_asleep > 0]
        return sum(value * weight for value, weight in known) / sum(weight for _, weight in known) if known else None

    return SleepSession(
        start=first.start,
        end=last.end,
        episode_id=getattr(first, "episode_id", None),
        efficiency=min(1.0, actual / elapsed),
        rating=latest("rating"),  # one final available morning report per episode
        hrv_ms=latest("hrv_ms"),  # one reading per night, not per fragment
        latency_min=first.latency_min,  # onset belongs to the beginning of sleep
        deep_pct=weighted_stage("deep_pct"),
        rem_pct=weighted_stage("rem_pct"),
        awakenings=sum(s.awakenings for s in fragments if s.awakenings is not None)
        if any(s.awakenings is not None for s in fragments) else None,
        resting_hr=latest("resting_hr"),
    )


def primary_nights(sessions: list[SleepSession], now: datetime | None = None) -> list[SleepSession]:
    """Summarise primary sleep, using completed episodes when ``now`` is set.

    Naps are never included. Explicit IDs must identify one episode, so a source
    reusing the same ID across an unsupported >24-hour span is rejected.
    Group before applying the cutoff: a completed early fragment does not make
    a known ongoing night into a completed observation.
    """
    records = [
        s for s in normalise_sessions(sessions)
        if not s.is_nap
    ]
    groups: list[list[SleepSession]] = []
    explicit: dict[str, list[SleepSession]] = {}
    for session in records:
        episode_id = getattr(session, "episode_id", None)
        if episode_id is not None:
            if episode_id not in explicit:
                explicit[episode_id] = []
                groups.append(explicit[episode_id])
            explicit[episode_id].append(session)
            continue
        previous = groups[-1] if groups else None
        if (
            previous
            and getattr(previous[0], "episode_id", None) is None
            and sleep_day(previous[-1]) == sleep_day(session)
            and hours_between(previous[-1].end, session.start) <= LEGACY_MAX_GAP_H
            and hours_between(previous[0].start, session.end) <= 24
        ):
            previous.append(session)
        else:
            groups.append([session])
    summaries = sorted((_summary(group) for group in groups), key=lambda s: instant(s.end))
    for previous, current in zip(summaries, summaries[1:]):
        if instant(current.start) < instant(previous.end):
            raise ValueError("primary episode IDs interleave; reconcile the source episode identities")
    return [s for s in summaries if now is None or instant(s.end) <= instant(now)]
