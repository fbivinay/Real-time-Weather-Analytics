import json
from datetime import datetime, timedelta, timezone

import pytest

from serving.engine import Engine, persist, write_redis
from serving.ops import Ops
from serving.tests.fakes import FakeRedis
from simulator.sim import Simulator
from weatherops.rainfall import HourlyRain

T0 = datetime(2025, 7, 1, tzinfo=timezone.utc)
COEFS = [-1.089, -9.163, 8.7489, -4.2773]


class FakeStore:
    def __init__(self):
        self.writes = []

    def write(self, orders, items, gone, rollup, weather, sim_now, predictions=None):
        self.writes.append(dict(orders=orders, items=items, gone=gone, rollup=rollup, weather=weather,
                                predictions=predictions))


@pytest.fixture(scope="module")
def engine():
    hourly = HourlyRain.load("data/rain_hourly.csv.gz")
    sim = Simulator(hourly, T0, seed=5, dup_rate=0, invalid_rate=0)
    eng = Engine(Ops(hourly, COEFS))
    t = T0
    while t < T0 + timedelta(hours=30):
        t += timedelta(minutes=1)
        for e in sim.step(t):
            eng.on_event(e, wall=1000.0)
    return eng


def test_tick_writes_every_view_and_publishes(engine):
    r = FakeRedis()
    result = engine.tick(now_wall=1010.0, lag=0)
    write_redis(r, result)
    ov = json.loads(r.get("state:overview"))
    assert ov["kpis"]["in_transit"] > 0 and ov["alerts"]
    assert set(ov["top"]) == {"states", "cities", "routes", "warehouses"}
    assert len(json.loads(r.get("state:forecast"))["MUM"]) == 48
    assert len(json.loads(r.get("state:future"))["timeline"]) == 48
    msg = json.loads(r.published[-1][1])
    assert msg["type"] == "tick" and "cities" in msg["impact"]
    assert json.loads(r.get("health:engine"))["consumer_lag"] == 0


def test_events_per_second_uses_the_last_minute(engine):
    eng = Engine(engine.ops)
    for w in (100.0, 130.0, 150.0, 155.0):
        eng.arrivals.append(w)
    assert eng.events_per_s(160.0) == pytest.approx(round(4 / 60, 1))
    assert eng.events_per_s(200.0) == pytest.approx(round(2 / 60, 1))
    assert len(eng.arrivals) == 2


def test_persist_flushes_orders_rollup_and_predictions(engine):
    store = FakeStore()
    result = engine.tick(now_wall=2000.0, lag=0)
    persist(store, engine.ops, result)
    w = store.writes[0]
    assert w["orders"] and w["items"] and w["rollup"]
    assert len(w["weather"]) == 40 and w["predictions"]
    persist(store, engine.ops, engine.tick(now_wall=2001.0, lag=0))
    assert store.writes[1]["orders"] == [] and store.writes[1]["predictions"] is None


def test_spark_windows_sum_cities(engine):
    eng = Engine(engine.ops)
    for start in ("a", "b", "c", "d", "e"):
        for city in ("MUM", "BLR"):
            eng.on_metric({"window_start": start, "city_id": city, "events": 10, "created": 3})
    assert [w["events"] for w in eng.windows] == [20, 20]
