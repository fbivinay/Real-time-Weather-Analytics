from datetime import datetime, timedelta, timezone

import pytest

from weatherops import risk

T = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
MIN = timedelta(minutes=1)


def inputs(rain=0.0, accum=0.0, gust=20.0, temp=30.0, rh=50.0, vis=10000.0):
    return {"rain_mmph": rain, "rain_accum_mm": accum, "gust_kmph": gust,
            "temperature_c": temp, "humidity_pct": rh, "visibility_m": vis}


def test_interp_hits_anchors_and_interpolates_between():
    a = risk.ANCHORS["rain_intensity"]
    assert risk.interp(15, a) == 0.5
    assert risk.interp(11.25, a) == pytest.approx(0.4)
    assert risk.interp(0, a) == 0
    assert risk.interp(500, a) == 1


def test_interp_for_a_decreasing_hazard():
    v = risk.ANCHORS["visibility"]
    assert risk.interp(10000, v) == 0
    assert risk.interp(50, v) == 1
    assert risk.interp(10, v) == 1
    assert risk.interp(300, v) == pytest.approx((0.775 + 0.5) / 2)


def test_heat_index_dry_extreme_heat():
    assert risk.heat_index_c(46, 15) == pytest.approx(45.9, abs=0.5)


def test_heat_index_mild():
    assert risk.heat_index_c(30, 40) == pytest.approx(29.7, abs=1)
    assert risk.heat_index_c(20, 60) == pytest.approx(19.8, abs=1)


@pytest.mark.parametrize("cutoff,expected", [(0, "low"), (24, "low"), (25, "medium"), (49, "medium"),
                                             (50, "high"), (74, "high"), (75, "critical"), (100, "critical")])
def test_category_thresholds(cutoff, expected):
    assert risk.category(cutoff) == expected


def test_calibration_cyclone_michaung_peak_is_critical():
    a = risk.assess(inputs(rain=26, gust=90, temp=25, rh=95, vis=820))
    assert a["category"] == "critical"
    assert a["hazard"] in {"rain", "wind"}


def test_calibration_ordinary_mumbai_monsoon_hour_is_low():
    a = risk.assess(inputs(rain=5, gust=30, temp=28, rh=85, vis=6000))
    assert a["category"] == "low"


def test_calibration_delhi_dry_heat_is_high():
    a = risk.assess(inputs(temp=46, rh=15, gust=30, vis=8000))
    assert a["category"] == "high"
    assert a["hazard"] == "heat"


def test_calibration_dense_fog_is_critical():
    a = risk.assess(inputs(vis=150, temp=12, rh=98, gust=5))
    assert a["category"] == "critical"
    assert a["hazard"] == "fog"


def test_calibration_calm_day_is_low_with_no_hazard():
    a = risk.assess(inputs())
    assert a["score"] == 0
    assert a["category"] == "low"
    assert a["hazard"] is None


def test_two_moderate_hazards_compound():
    a = risk.assess(inputs(rain=15, gust=62))
    assert a["factors"]["rain"] == pytest.approx(0.5)
    assert a["factors"]["wind"] == pytest.approx(0.5)
    assert a["score"] == 75


def test_accumulation_can_drive_rain_risk():
    a = risk.assess(inputs(rain=1, accum=100))
    assert a["factors"]["rain"] == pytest.approx(0.775)


def test_missing_inputs_contribute_nothing():
    a = risk.assess({"rain_mmph": None, "gust_kmph": None, "temperature_c": None,
                     "humidity_pct": None, "visibility_m": None, "rain_accum_mm": None})
    assert a["score"] == 0


def test_climatology_boost_when_unusual_for_the_place():
    base = risk.assess(inputs(rain=15))
    clim = {"rain_p95": 8.0, "gust_p95": 60.0, "heat_p95": 40.0, "vis_p5": 1000.0}
    boosted = risk.assess(inputs(rain=15), clim=clim)
    assert boosted["unusual"] is True
    assert boosted["score"] == round(base["score"] * 1.15)
    usual = risk.assess(inputs(rain=15), clim={**clim, "rain_p95": 30.0})
    assert usual["unusual"] is False
    assert usual["score"] == base["score"]


def test_forecast_flags_developing_without_inflating_the_observed_score():
    a = risk.assess(inputs(), forecast_inputs=inputs(rain=40, gust=80))
    assert a["developing"] is True
    assert a["forecast_category"] == "critical"
    assert a["score"] == 0 and a["category"] == "low"      # incidents follow observations
    assert a["hazard"] in {"rain", "wind"}


def test_forecast_lower_than_now_changes_nothing():
    now = risk.assess(inputs(rain=30))
    with_fc = risk.assess(inputs(rain=30), forecast_inputs=inputs(rain=2))
    assert with_fc["score"] == now["score"]
    assert with_fc["developing"] is False


def window(to, rain=0.0, gust=20.0, vis=10000.0, temp=30.0, rh=50.0, rain_24h=None):
    return {"observed_from": to, "observed_to": to, "rain_avg": rain, "gust_max": gust, "visibility_min": vis,
            "temp_avg": temp, "humidity_avg": rh, "rain_24h": rain_24h}


def test_summarize_uses_windows_from_the_last_15_observed_minutes():
    ws = [window(T - 20 * MIN, rain=10, gust=70), window(T - 10 * MIN, rain=20, gust=30), window(T, rain=30, vis=900)]
    s = risk.summarize(ws, T)
    assert s["rain_mmph"] == pytest.approx(25)
    assert s["gust_kmph"] == 30
    assert s["visibility_m"] == 900
    assert s["temperature_c"] == 30


def test_summarize_falls_back_to_the_latest_window_when_time_is_compressed():
    # replay at 120x: windows an observed hour apart
    ws = [window(T - 60 * MIN, rain=5), window(T, rain=12)]
    assert risk.summarize(ws, T)["rain_mmph"] == 12


def test_summarize_integrates_rain_over_three_hours_plus_antecedent():
    ws = [window(T - k * 10 * MIN, rain=6) for k in range(30, -1, -1)]   # 5 h of 6 mm/h
    s = risk.summarize(ws, T, rain_24h=40)
    # 3 h x 6 mm/h = 18 mm, plus a quarter of the 40 mm antecedent
    assert s["rain_accum_mm"] == pytest.approx(18 + 10, abs=0.01)


def test_summarize_reads_antecedent_from_reference_windows():
    ws = [window(T, rain=0, rain_24h=80)]
    assert risk.summarize(ws, T)["rain_accum_mm"] == pytest.approx(20)


def test_summarize_of_nothing_is_empty_inputs():
    assert risk.summarize([], T)["rain_mmph"] is None
