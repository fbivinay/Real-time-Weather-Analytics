import json
from collections import Counter
from datetime import datetime, timedelta, timezone

import pytest

from simulator.sim import Simulator
from weatherops import company as co
from weatherops import events
from weatherops.rainfall import HourlyRain

T0 = datetime(2025, 7, 10, 0, tzinfo=timezone.utc)


def run(hourly, hours=30, faults=0.0, seed=1):
    sim = Simulator(hourly, T0, seed=seed, dup_rate=faults, invalid_rate=faults)
    out = []
    t = T0
    while t < T0 + timedelta(hours=hours):
        t += timedelta(minutes=1)
        out += sim.step(t)
    return sim, out


@pytest.fixture(scope="module")
def dry():
    return run(HourlyRain({}))


def test_events_are_valid_and_typed(dry):
    _, evs = dry
    assert all(events.validate(e, now=datetime.now(timezone.utc)) is None for e in evs)
    kinds = Counter(e["type"] for e in evs)
    for t in ("ORDER_CREATED", "ORDER_ASSIGNED", "VEHICLE_DISPATCHED", "VEHICLE_MOVEMENT",
              "HUB_ARRIVAL", "DELIVERY_COMPLETED", "WEATHER_EVENT"):
        assert kinds[t] > 0, t


def test_volume_matches_company_scale(dry):
    _, evs = dry
    created = sum(e["type"] == "ORDER_CREATED" for e in evs)
    assert 20_000 < created / 30 * 24 < 35_000


def test_lifecycle_is_ordered_per_order(dry):
    _, evs = dry
    seen = {}
    order = ["ORDER_CREATED", "ORDER_ASSIGNED", "DELIVERY_COMPLETED"]
    for e in evs:
        if e["type"] in order:
            seen.setdefault(e["order_id"], []).append(e["type"])
    complete = [v for v in seen.values() if "DELIVERY_COMPLETED" in v]
    assert complete and all(v == order for v in complete)


def test_scheduled_orders_reach_two_days_out(dry):
    _, evs = dry
    leads = [(events.parse_ts(json.loads(e["payload"])["planned_dispatch"]) - events.parse_ts(e["sim_time"]))
             .total_seconds() / 3600 for e in evs if e["type"] == "ORDER_CREATED"]
    assert min(leads) >= 1.5 and max(leads) > 36


def test_dry_weather_has_no_weather_delay(dry):
    _, evs = dry
    assert not [e for e in evs if e["type"] == "DELIVERY_DELAY"]
    done = [json.loads(e["payload"]) for e in evs if e["type"] == "DELIVERY_COMPLETED"]
    assert all(d["weather_delay_min"] == 0 for d in done)


def test_heavy_rain_delays_trips_through_it():
    wet = {(c, (T0 + timedelta(hours=h)).strftime("%Y-%m-%dT%H")): 12.0 for c in ("MUM", "PUN") for h in range(40)}
    _, evs = run(HourlyRain(wet))
    delays = [e for e in evs if e["type"] == "DELIVERY_DELAY"]
    assert delays and all(json.loads(e["payload"])["cause"] == "heavy" for e in delays)
    done = [json.loads(e["payload"]) for e in evs if e["type"] == "DELIVERY_COMPLETED"]
    mum = [d for d in done if d["route_code"].endswith("MUM")]
    assert mum and sum(d["weather_delay_min"] for d in mum) / len(mum) > 20


def test_faults_inject_duplicates_and_invalid_events():
    _, evs = run(HourlyRain({}), hours=6, faults=0.01)
    ids = Counter(e["event_id"] for e in evs)
    assert any(n > 1 for n in ids.values())
    assert any(events.validate(e, now=datetime.now(timezone.utc)) for e in evs)
