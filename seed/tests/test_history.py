from datetime import date

import numpy as np
import pytest

from seed import history
from weatherops import company as co


@pytest.fixture(scope="module")
def frame():
    # Two weeks of a wet July in Mumbai's region and dry elsewhere.
    days = [date(2024, 7, d) for d in range(1, 15)]
    rain = {(c, d.isoformat()): (90.0 if c in ("MUM", "PUN") else 0.0) for c in co.NETWORK.cities for d in days}
    return history.build(days, rain, seed=1)


def test_columns_are_internally_consistent(frame):
    assert (frame["exposed"] <= frame["orders"]).all()
    assert (frame["affected"] <= frame["exposed"]).all()
    assert (frame["sla_breaches"] <= frame["orders"]).all()
    assert (frame["delay_min_sum"] >= 0).all()
    assert set(frame["severity"]) <= {"none", "rain", "heavy", "extreme"}


def test_dry_routes_have_no_weather_impact(frame):
    dry = frame[frame["severity"] == "none"]
    assert dry["exposed"].sum() == 0 and dry["cost_transport"].sum() == 0


def test_wet_routes_carry_the_delay(frame):
    wet = frame[frame["route_id"] == co.NETWORK.route_by_code["MUM → MUM"].id]
    assert (wet["severity"] == "heavy").all()
    assert wet["affected"].sum() > 0.5 * wet["exposed"].sum() > 0


def test_volume_is_company_scale(frame):
    per_day = frame.groupby("date")["orders"].sum()
    assert 15_000 < per_day.mean() < 40_000


def test_route_impact_and_breach_model():
    days = [date(2024, 7, d) for d in range(1, 29)]
    rng = np.random.default_rng(3)
    rain = {(c, d.isoformat()): float(rng.choice([0, 10, 80, 150], p=[0.5, 0.3, 0.15, 0.05]))
            for c in co.NETWORK.cities for d in days}
    f = history.build(days, rain, seed=2)
    impact = history.route_impact(f)
    heavy = impact[(impact.severity == "heavy")]
    none = impact[(impact.severity == "none")]
    assert (heavy.delay_ratio > 0).all() and (none.delay_ratio == 0).all()
    assert none.on_time.mean() > heavy.on_time.mean()
    coefs, metrics = history.fit_breach_model(seed=4, n=40_000)
    assert coefs[1] < 0 and coefs[2] > 0       # more slack -> fewer breaches; more rain -> more
    assert metrics["auc"] > 0.75
