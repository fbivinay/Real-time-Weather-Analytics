import json
from datetime import datetime, timedelta, timezone

import pytest

from serving.ops import Ops
from simulator.sim import Simulator
from weatherops import events
from weatherops.rainfall import HourlyRain

COEFS = [-1.089, -9.163, 8.7489, -4.2773]
T0 = datetime(2025, 7, 1, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def hourly():
    return HourlyRain.load("data/rain_hourly.csv.gz")


@pytest.fixture(scope="module")
def ops(hourly):
    sim = Simulator(hourly, T0, seed=3, dup_rate=0, invalid_rate=0)
    o = Ops(hourly, COEFS)
    t = T0
    while t < T0 + timedelta(hours=36):
        t += timedelta(minutes=1)
        for e in sim.step(t):
            o.apply(e)
    o.compute_risk()
    return o


def test_future_windows_grow_and_are_explained(ops):
    s = ops.future_summary()
    assert s["6"]["scheduled"] <= s["12"]["scheduled"] <= s["24"]["scheduled"] <= s["48"]["scheduled"]
    assert s["48"]["scheduled"] > 10_000
    a = next(iter(ops.cohort_risk.values()))
    assert len(a["contributions"]) == 7 and all("detail" in c for c in a["contributions"])


def test_monsoon_creates_exposure_and_risk(ops):
    s = ops.future_summary()["48"]
    assert s["exposed"] > 0
    assert s["high"] + s["critical"] > 0
    crit = ops.critical_orders()
    assert crit and crit[0]["category"] in ("High", "Critical")


def test_live_kpis_are_consistent(ops):
    live = ops.live()
    assert live["in_transit"] > 0 and live["delivered_today"] > 0
    assert live["weather_exposed"] <= live["in_transit"]
    assert 0 <= live["on_time_rate"] <= 1


def test_impact_rolls_up_every_level(ops):
    imp = ops.impact()
    assert set(imp) == {"routes", "cities", "states", "warehouses", "hubs"}
    assert len(imp["cities"]) == 40 and len(imp["warehouses"]) == 10
    total = sum(r["orders"] for r in imp["routes"].values())
    assert sum(c["orders"] for c in imp["cities"].values()) == total
    assert sum(w["orders"] for w in imp["warehouses"].values()) == total
    alerts = ops.alerts(imp, ops.future_summary(), ops.live())
    assert alerts and all(a["text"] for a in alerts)


def test_timeline_covers_48_hours(ops):
    tl = ops.timeline()
    assert len(tl) == 48 and sum(b["orders"] for b in tl) > 0
    assert all(0 <= b["rain_probability"] <= 1 for b in tl)


def test_rollup_counts_every_delivery(ops):
    delivered = sum(1 for o in ops.orders.values() if o["status"] == "delivered")
    rows = ops.take_rollup()
    assert sum(v["orders"] for _, v, _ in rows) == delivered
    assert ops.take_rollup() == []


def test_replay_wrap_resets_state(hourly):
    o = Ops(hourly, COEFS)
    e = events.make("WEATHER_EVENT", T0 + timedelta(days=40), {"rain_mmph": 3, "severity": "rain"}, city_id="MUM")
    o.apply(e)
    o.orders["X"] = {}
    back = events.make("WEATHER_EVENT", T0, {"rain_mmph": 0, "severity": "none"}, city_id="MUM")
    assert o.apply(back) is True
    assert o.orders == {} and o.resets == 1


def test_prune_forgets_old_deliveries(ops):
    before = len(ops.orders)
    ops.sim_now += timedelta(days=3)
    gone = ops.prune()
    ops.sim_now -= timedelta(days=3)
    assert gone and len(ops.orders) == before - len(gone)
    json.dumps([str(g) for g in gone])


def test_critical_orders_show_each_cohort_once(ops):
    rows = ops.critical_orders(50)
    keys = [(r["route_id"], r["tier"], r["planned_dispatch"]) for r in rows]
    assert len(keys) == len(set(keys))
    assert sum(r["similar"] + 1 for r in rows) >= len(rows)
