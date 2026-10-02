from datetime import datetime, timezone

import pytest

from weatherops import rainfall as rf


@pytest.mark.parametrize("mm,cls", [(0, "none"), (2.4, "none"), (2.5, "rain"), (64.4, "rain"),
                                    (64.5, "heavy"), (115.5, "heavy"), (115.6, "extreme")])
def test_daily_class_follows_imd_edges(mm, cls):
    assert rf.daily_class(mm) == cls


@pytest.mark.parametrize("mmph,cls", [(2.4, "none"), (2.5, "rain"), (7.6, "heavy"), (20, "extreme")])
def test_hourly_class(mmph, cls):
    assert rf.hourly_class(mmph) == cls


def test_seasons():
    assert [rf.season(m) for m in (1, 4, 7, 11)] == ["winter", "pre-monsoon", "monsoon", "post-monsoon"]


def test_climatology_stats():
    rows = [("MUM", f"2020-07-{d:02d}", mm) for d, mm in zip(range(1, 11), [0, 0, 3, 10, 20, 70, 80, 120, 5, 2])]
    stats = rf.climatology(rows)[("MUM", 7)]
    assert stats["days"] == 10
    assert stats["mean_mm"] == pytest.approx(31.0)
    assert stats["median_mm"] == pytest.approx(7.5)
    assert stats["p_rainy"] == pytest.approx(0.7)
    assert stats["p_heavy"] == pytest.approx(0.3)
    assert stats["p90_mm"] >= stats["p75_mm"] >= stats["median_mm"]


def test_hourly_rain_lookup_and_forecast():
    t0 = datetime(2025, 7, 1, 6, tzinfo=timezone.utc)
    hourly = rf.HourlyRain({("MUM", t0.strftime("%Y-%m-%dT%H")): 12.0})
    assert hourly.mmph("MUM", t0) == 12.0
    assert hourly.mmph("MUM", t0.replace(hour=7)) == 0.0
    near = rf.forecast(hourly, "MUM", t0, lead_h=1)
    far = rf.forecast(hourly, "MUM", t0, lead_h=40)
    assert sum(near["probs"].values()) == pytest.approx(1, abs=1e-3)
    assert max(near["probs"], key=near["probs"].get) == "heavy"
    assert near["probs"]["heavy"] > far["probs"]["heavy"]
    assert near["p_rain"] > 0.8
