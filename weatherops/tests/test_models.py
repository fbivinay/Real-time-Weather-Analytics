from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from weatherops import company as co
from weatherops import delay, risk, scenario

T0 = datetime(2025, 7, 10, 4, tzinfo=timezone.utc)
ROUTE = co.NETWORK.route_by_code["BLR → MYS"]
DRY = {"none": 1.0, "rain": 0.0, "heavy": 0.0, "extreme": 0.0}
HEAVY = {"none": 0.0, "rain": 0.1, "heavy": 0.8, "extreme": 0.1}
HIST = {c: {"delay_ratio": (delay.MULTIPLIER[c] - 1) * ROUTE.sensitivity, "on_time": 0.93} for c in delay.CLASSES}
COEFS = [-3.0, -2.0, 6.0, 0.5]


def const_forecast(probs):
    return lambda city_id, when, lead_h: {"probs": probs, "p_rain": 1 - probs["none"], "mmph": 0}


def test_weather_eta_follows_class_multiplier_and_sensitivity():
    normal = co.normal_eta_h(ROUTE)
    assert delay.weather_eta_h(ROUTE, "none") == pytest.approx(normal)
    assert delay.weather_delay_h(ROUTE, "heavy") == pytest.approx(normal * 0.30 * ROUTE.sensitivity)
    assert delay.weather_eta_h(ROUTE, "extreme") > delay.weather_eta_h(ROUTE, "heavy")


def test_exposure_dry_vs_wet():
    dry = delay.exposure(ROUTE, T0, T0, const_forecast(DRY))
    wet = delay.exposure(ROUTE, T0, T0, const_forecast(HEAVY))
    assert dry["exposed_share"] == 0 and dry["expected_class"] == "none"
    assert wet["exposed_share"] == 1 and wet["expected_class"] == "heavy"
    assert wet["hours"] >= 5


def test_risk_contributions_sum_to_score_and_respect_caps():
    wx = delay.exposure(ROUTE, T0, T0, const_forecast(HEAVY))
    r = risk.assess(ROUTE, "express", T0, wx, HIST, wh_exposed_share=0.8, coefs=COEFS)
    assert sum(c["points"] for c in r["contributions"]) == pytest.approx(r["score"], abs=0.6)
    for c in r["contributions"]:
        assert 0 <= c["points"] <= risk.CAPS[c["key"]]
    assert r["category"] in ("High", "Critical")
    assert r["expected_delay_min"] > 30
    assert 0.5 < r["p_breach"] <= 1


def test_dry_economy_order_is_low_risk():
    wx = delay.exposure(ROUTE, T0, T0, const_forecast(DRY))
    r = risk.assess(ROUTE, "economy", T0, wx, HIST, wh_exposed_share=0.0, coefs=COEFS)
    assert r["category"] == "Low" and r["expected_delay_min"] == 0 and r["p_breach"] < 0.1


@pytest.mark.parametrize("score,cat", [(0, "Low"), (24.9, "Low"), (25, "Medium"), (50, "High"), (75, "Critical")])
def test_category_edges(score, cat):
    assert risk.category(score) == cat


def test_logistic_fit_recovers_coefficients():
    rng = np.random.default_rng(0)
    X = np.column_stack([np.ones(20000), rng.normal(size=20000), rng.normal(size=20000)])
    true = np.array([-1.0, 2.0, -0.5])
    y = rng.random(20000) < 1 / (1 + np.exp(-X @ true))
    assert np.allclose(risk.fit_logistic(X, y), true, atol=0.1)


def _cohorts(route, n=100):
    return [{"route_id": route.id, "city_id": route.city_id, "tier": "standard", "dispatch": T0, "count": n}]


def test_scenario_rain_scale_increases_delay():
    fc = const_forecast({"none": 0.2, "rain": 0.6, "heavy": 0.2, "extreme": 0.0})
    base = scenario.evaluate(_cohorts(ROUTE), T0, fc, COEFS)
    worse = scenario.evaluate(_cohorts(ROUTE), T0, fc, COEFS, {"rain_scale": 2.0})
    assert worse["avg_delay_min"] > base["avg_delay_min"]
    assert worse["cost_inr"] > base["cost_inr"]


def test_scenario_dispatch_shift_moves_out_of_the_storm():
    def storm(city_id, when, lead_h):
        probs = HEAVY if when < T0 + timedelta(hours=8) else DRY
        return {"probs": probs, "p_rain": 1 - probs["none"], "mmph": 0}
    base = scenario.evaluate(_cohorts(ROUTE), T0, storm, COEFS)
    later = scenario.evaluate(_cohorts(ROUTE), T0, storm, COEFS, {"dispatch_shift_h": 8})
    assert later["exposed"] < base["exposed"] and later["avg_delay_min"] < base["avg_delay_min"]


def test_scenario_reroute_uses_the_drier_warehouse():
    primary, secondary = co.NETWORK.routes_to["MYS"]
    wet_origin = co.NETWORK.warehouses[primary.warehouse_id].city_id

    def fc(city_id, when, lead_h):
        probs = HEAVY if city_id == wet_origin else DRY
        return {"probs": probs, "p_rain": 1 - probs["none"], "mmph": 0}
    base = scenario.evaluate(_cohorts(primary), T0, fc, COEFS)
    moved = scenario.evaluate(_cohorts(primary), T0, fc, COEFS, {"reroute": True})
    assert moved["exposed"] < base["exposed"]
    assert moved["rerouted"] == 100


def test_scenario_local_hub_cuts_long_haul_delay():
    route = max(co.NETWORK.routes.values(), key=lambda r: r.distance_km)
    fc = const_forecast(HEAVY)
    base = scenario.evaluate(_cohorts(route), T0, fc, COEFS)
    hub = scenario.evaluate(_cohorts(route), T0, fc, COEFS, {"add_hub": route.city_id})
    assert hub["avg_delay_min"] < base["avg_delay_min"]
