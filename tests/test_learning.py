import pytest
from factories import at, nap, night

from app.schemas import AgeBand, Chronotype, UserProfile
from app.services import learning

NOW = at(0, "12:00")


def profile(**kw) -> UserProfile:
    return UserProfile(name="Test", **kw)


@pytest.mark.parametrize(
    ("band", "need"),
    [(AgeBand.TEEN, 9.0), (AgeBand.YOUNG_ADULT, 8.0), (AgeBand.ADULT, 8.0), (AgeBand.OLDER, 7.5)],
)
def test_age_band_default_sleep_need(band, need):
    assert learning.prior_need_h(profile(age_band=band)) == need


def test_need_setting_stays_inside_the_healthy_range():
    assert learning.prior_need_h(profile(age_band=AgeBand.TEEN, sleep_need_h=7)) == 8  # teens: never below 8
    assert learning.prior_need_h(profile(sleep_need_h=10)) == 9
    belief = learning.sleep_need_belief(profile(age_band=AgeBand.TEEN), [])
    assert belief.grid[0][0] == 8.0 and belief.grid[-1][0] == 10.0


def test_sleep_need_without_check_ins_is_the_prior():
    b = learning.sleep_need_belief(profile(), [])
    assert b.mean == pytest.approx(8.0, abs=0.05)
    assert b.n == 0 and 7 <= b.low < b.mean < b.high <= 9


def test_morning_check_ins_move_the_need_belief():
    refreshed_after_7h = [night(-k, "23:30", "07:20", rating=5) for k in range(10, 0, -1)]
    wrecked_after_8h = [night(-k, "22:30", "07:25", rating=1) for k in range(10, 0, -1)]
    assert learning.sleep_need_belief(profile(), refreshed_after_7h).mean < 7.6
    assert learning.sleep_need_belief(profile(), wrecked_after_8h).mean > 8.5


def test_more_check_ins_tighten_the_belief():
    nights = [night(-k, rating=3) for k in range(14, 0, -1)]
    few = learning.sleep_need_belief(profile(), nights[-2:])
    many = learning.sleep_need_belief(profile(), nights)
    assert many.sd < few.sd


@pytest.mark.parametrize(
    ("band", "chronotype", "mid"),
    [
        (AgeBand.ADULT, Chronotype.NEUTRAL, 3.5),  # usual 23:30-07:30
        (AgeBand.YOUNG_ADULT, Chronotype.NEUTRAL, 4.0),  # +30 min for 13-25
        (AgeBand.OLDER, Chronotype.NEUTRAL, 3.0),  # -30 min for 65+
        (AgeBand.ADULT, Chronotype.LATE, 4.5),
        (AgeBand.ADULT, Chronotype.EARLY, 2.5),
    ],
)
def test_body_clock_prior_from_age_band_and_chronotype(band, chronotype, mid):
    b = learning.body_clock_belief(profile(age_band=band, chronotype=chronotype), [], NOW)
    assert b.mean == pytest.approx(mid) and b.n == 0


def test_body_clock_learns_from_your_own_nights():
    late_nights = [night(-k, "01:00", "09:00") for k in range(14, 0, -1)]  # mid-sleep 05:00
    b = learning.body_clock_belief(profile(age_band=AgeBand.ADULT), late_nights, NOW)
    assert 4.5 < b.mean < 5.0 and b.n == 14


def test_no_naps_means_average_grogginess():
    assert learning.inertia_belief([]).mean == pytest.approx(1.0, abs=0.02)


def test_grogginess_sensitivity_learns_from_nap_ratings():
    groggy = [nap(-k, "14:00", 50, rating=1) for k in range(1, 6)]
    fresh = [nap(-k, "14:00", 50, rating=5) for k in range(1, 6)]
    assert learning.inertia_belief(groggy).mean > 1.2
    assert learning.inertia_belief(fresh).mean < 0.9


def test_latency_shrinks_towards_your_setting():
    sessions = [night(-k, latency_min=30) for k in range(5, 0, -1)]
    assert learning.latency_min(profile(sleep_latency_min=10), sessions) == pytest.approx(20)
