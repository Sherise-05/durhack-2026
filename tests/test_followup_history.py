"""One primary night is one observation, regardless of wearable fragmentation."""
from datetime import datetime, timedelta

import pytest
from factories import at, night, user

from app.schemas import SleepSession
from app.services import learning, planner, sleep_model as sm
from app.services.sleep_history import primary_nights
from app.time_utils import instant


def histories(rate_both=False):
    whole, split = [], []
    for k in range(7, 0, -1):
        whole.append(night(-k, '23:00', '07:00', eff=1, rating=4, hrv=60))
        split += [
            SleepSession(start=at(-k,'23:00'), end=at(1-k,'03:00'), efficiency=1,
                         rating=4 if rate_both else None, hrv_ms=60),
            SleepSession(start=at(1-k,'03:00'), end=at(1-k,'07:00'), efficiency=1,
                         rating=4, hrv_ms=60),
        ]
    return user(whole), user(split)


@pytest.mark.parametrize('rate_both', [False, True])
def test_fragmentation_does_not_change_personalisation_or_readiness(rate_both):
    whole, split = histories(rate_both)
    now = at(0,'12:00')
    a, b = learning.personalise(whole, now), learning.personalise(split, now)
    assert a.need == b.need
    assert a.need.n == b.need.n == 7
    assert a.body_clock == b.body_clock
    x, y = planner.snapshot(whole, now), planner.snapshot(split, now)
    assert x.last_night.hours_asleep == y.last_night.hours_asleep == 8
    assert x.readiness == y.readiness
    assert sm.PersonModel(whole,now).state.pressure == pytest.approx(sm.PersonModel(split,now).state.pressure)
    assert sm.hrv_vs_baseline(whole.sessions,now) == sm.hrv_vs_baseline(split.sessions,now)
    assert len(sm.nightly_stats(split.sessions, 8, now)) == 7


def test_gap_counts_awake_while_actual_sleep_is_summed():
    fragments = [
        SleepSession(start=at(-1,'23:00'),end=at(0,'03:00'),efficiency=1),
        SleepSession(start=at(0,'04:00'),end=at(0,'07:00'),efficiency=1,rating=4),
    ]
    episode = primary_nights(fragments)[0]
    assert len(primary_nights(fragments)) == 1
    assert episode.hours_asleep == 7
    assert episode.efficiency == 7/8
    assert episode.start == at(-1,'23:00') and episode.end == at(0,'07:00')
    # Raw intervals remain separate, so pressure builds during the hour awake.
    model = sm.PersonModel(user(fragments), at(0,'12:00'))
    assert len(model.blocks) == 2
    assert model.state_at(at(0,'03:30')).asleep is False


def test_one_final_rating_and_hrv_reading_per_primary_night():
    fragments = [
        SleepSession(start=at(-1,'23:00'),end=at(0,'03:00'),rating=1,hrv_ms=50,latency_min=10),
        SleepSession(start=at(0,'03:00'),end=at(0,'07:00'),rating=4,hrv_ms=60,latency_min=20),
    ]
    episode = primary_nights(fragments)[0]
    assert episode.rating == 4 and episode.hrv_ms == 60
    assert episode.latency_min == 10
    assert learning.sleep_need_belief(user().profile,fragments).n == 1


def test_explicit_episode_id_can_group_long_gap_across_legacy_boundary():
    fragments = [
        SleepSession(start=at(0,'10:00'),end=at(0,'13:00'),efficiency=1,episode_id='night-A'),
        SleepSession(start=at(0,'16:00'),end=at(0,'19:00'),efficiency=1,episode_id='night-A'),
    ]
    summary = primary_nights(fragments)
    assert len(summary) == 1
    assert summary[0].episode_id == 'night-A' and summary[0].hours_asleep == 6


def test_distinct_explicit_episodes_do_not_merge_just_because_adjacent():
    fragments = [
        SleepSession(start=at(-1,'23:00'),end=at(0,'03:00'),episode_id='A'),
        SleepSession(start=at(0,'03:00'),end=at(0,'07:00'),episode_id='B'),
    ]
    assert len(primary_nights(fragments)) == 2


def test_legacy_nights_do_not_join_across_sleep_days_or_long_gap():
    assert len(primary_nights([night(-2),night(-1)])) == 2
    fragments = [
        SleepSession(start=at(-1,'22:00'),end=at(0,'01:00')),
        SleepSession(start=at(0,'04:00'),end=at(0,'07:00')),
    ]
    assert len(primary_nights(fragments)) == 2


def test_ongoing_night_does_not_become_completed_evidence_from_early_fragment():
    fragments = [
        SleepSession(start=at(-1,'23:00'),end=at(0,'03:00'),efficiency=1,rating=1),
        SleepSession(start=at(0,'03:00'),end=at(0,'07:00'),efficiency=1,rating=4,hrv_ms=60),
    ]
    observed = primary_nights(fragments,now=at(0,'04:00'))
    assert observed == []
    personal = learning.personalise(user(fragments),at(0,'04:00'))
    assert personal.need.n == personal.body_clock.n == 0
    assert sm.sleep_debt_h(fragments,8,at(0,'04:00')) == 0
    assert sm.PersonModel(user(fragments),at(0,'04:00')).last_night is None


def test_repeated_hour_midpoint_is_between_actual_instants():
    record = SleepSession(start='2026-10-25T01:30:00+01:00',
                          end='2026-10-25T01:15:00+00:00',efficiency=1)
    midpoint = (learning.mid_sleep_since_noon(record.start,record.end)+12) % 24
    assert midpoint == pytest.approx(1.875)
    assert record.hours_asleep == 0.75


def test_ordinary_datetime_midpoint_preserves_seconds():
    start = datetime(2026,11,15,23,0,30)
    end = datetime(2026,11,16,7,0,30)
    assert (learning.mid_sleep_since_noon(start,end)+12) % 24 == pytest.approx(3+30/3600)
    assert learning.mid_sleep_since_noon(start.time(),end.time()) == pytest.approx(15+30/3600)


def test_explicit_episode_cannot_reuse_id_across_distinct_days():
    fragments = [night(-2).model_copy(update={'episode_id':'same'}),
                 night(-1).model_copy(update={'episode_id':'same'})]
    with pytest.raises(ValueError,match='at most 24 hours'):
        primary_nights(fragments)


def test_explicit_episode_crossing_sleepday_boundary_charges_need_once():
    fragments = [
        SleepSession(start=at(0,'10:00'),end=at(0,'13:00'),efficiency=1,episode_id='night-A'),
        SleepSession(start=at(0,'16:00'),end=at(0,'19:00'),efficiency=1,episode_id='night-A'),
    ]
    # Six hours of one primary episode against eight-hour need: two hours owed.
    assert sm.sleep_debt_h(fragments,8,at(0,'20:00')) == 2


def test_two_split_nights_cannot_fake_four_nights_of_hrv_baseline():
    fragments = []
    for k in (2,1):
        fragments += [
            SleepSession(start=at(-k,'23:00'),end=at(1-k,'03:00'),hrv_ms=50),
            SleepSession(start=at(1-k,'03:00'),end=at(1-k,'07:00'),hrv_ms=60),
        ]
    assert sm.hrv_vs_baseline(fragments,at(0,'12:00')) is None
