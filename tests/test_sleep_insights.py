"""My sleep insights: arithmetic over logged nights, no invented data."""

from datetime import date, datetime, timedelta

from app.schemas import NightStats
from app.services.sleep_insights import build

TODAY = date(2026, 11, 16)  # a Monday


def night(morning: date, bed: str, wake: str, hours: float, rating=None) -> NightStats:
    bh, bm = map(int, bed.split(":")); wh, wm = map(int, wake.split(":"))
    bed_day = morning if bh < 12 else morning - timedelta(days=1)
    return NightStats(morning=morning, bedtime=datetime.combine(bed_day, datetime.min.time()).replace(hour=bh, minute=bm),
                      wake=datetime.combine(morning, datetime.min.time()).replace(hour=wh, minute=wm),
                      hours_asleep=hours, efficiency=0.9, sleep_score=70, rating=rating)


def test_empty_history_asks_for_more_nights_and_charts_stay_blank():
    result = build([], need_h=8, debt_h=0, today=TODAY)
    assert result.nights_logged == 0 and result.recent_avg_h is None and result.regularity is None
    assert all(p["value"] is None for p in result.hours_chart["points"])  # missing is not zero
    assert len(result.hours_chart["points"]) == 14
    assert [i.title for i in result.insights] == ["More nights, more insight"]


def test_regular_short_sleeper_gets_short_and_steady_insights():
    nights = [night(TODAY - timedelta(days=d), "23:30", "07:00", 6.7) for d in range(7)]
    result = build(nights, need_h=8, debt_h=2.5, today=TODAY)
    assert result.regularity == "Very regular" and result.wake_sd_min == 0
    titles = [i.title for i in result.insights]
    assert "Running short" in titles and "Sleep debt" in titles and "Steady rhythm" in titles
    assert "Weekend shift" not in titles
    # Bars run from bedtime to wake on a clock measured from noon: 23:30 -> 690, 07:00 -> 1140.
    assert result.window_chart["points"][-1] == {"label": "Mon 16", "start": 690, "end": 1140}


def test_weekend_lie_ins_show_social_jet_lag_and_ratings_link_to_hours():
    nights = []
    for d in range(14):
        morning = TODAY - timedelta(days=d)
        if morning.weekday() >= 5:  # woke Sat/Sun: 2 h later
            nights.append(night(morning, "01:30", "10:00", 8.0, rating=5))
        else:
            nights.append(night(morning, "23:30", "07:30", 6.0 if d % 2 else 7.5, rating=2 if d % 2 else 4))
    result = build(nights, need_h=8, debt_h=0.5, today=TODAY)
    assert result.social_jet_lag_min == 135  # bed 2 h later, wake 2.5 h later
    assert result.regularity == "Irregular"
    titles = {i.title: i for i in result.insights}
    assert "later than on weekdays" in titles["Weekend shift"].body
    assert "Wake time moves around" in titles
    assert "What feeling rested takes" in titles
    assert "Sleep debt" not in titles  # under 1 h is not flagged
