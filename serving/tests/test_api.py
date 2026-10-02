"""API tests without Postgres: Redis-backed views, health, the WebSocket and
input validation. SQL endpoints are exercised against a real database in
test_api_pg.py when TEST_PG_DSN is set."""
import asyncio
import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from serving import api
from serving.tests.fakes import FakeRedis
from weatherops.rainfall import HourlyRain


def now_iso(delta_s=0):
    return (datetime.now(timezone.utc) - timedelta(seconds=delta_s)).strftime("%Y-%m-%dT%H:%M:%SZ")


def no_db():
    @contextmanager
    def connect():
        raise ConnectionError("no database in unit tests")
        yield
    return connect


def running():
    r = FakeRedis()
    r.set("state:overview", json.dumps({"sim_time": "2025-07-03T10:00:00Z", "kpis": {"in_transit": 900}}))
    r.set("state:impact", json.dumps({"cities": {}, "states": {}, "routes": {}, "warehouses": {}, "hubs": {}}))
    r.set("health:simulator", json.dumps({"at": now_iso(2), "sim_time": "2025-07-03T10:00:00Z"}))
    r.set("health:engine", json.dumps({"tick_at": now_iso(3), "freshness_s": 4, "events_per_s": 51.2,
                                       "consumer_lag": 0}))
    for q in ("clean", "metrics", "quarantine"):
        r.set(f"health:spark:{q}", json.dumps({"name": q, "at": now_iso(4)}))
    return r


def client(r, broadcaster=None):
    return TestClient(api.create_app(r, no_db(), HourlyRain({}), broadcaster=broadcaster))


def test_overview_is_503_until_the_engine_writes_it():
    with client(FakeRedis()) as c:
        assert c.get("/api/overview").status_code == 503
    with client(running()) as c:
        assert c.get("/api/overview").json()["kpis"]["in_transit"] == 900


def test_health_of_a_running_pipeline():
    h = api.build_health(running(), lambda: True)
    assert {k: v["status"] for k, v in h["components"].items()} == {
        "api": "ok", "redis": "ok", "database": "ok", "simulator": "ok", "spark": "ok", "engine": "ok",
        "stream": "ok", "kafka": "ok"}
    assert h["status"] == "ok"
    assert h["components"]["stream"]["events_per_s"] == 51.2


def test_health_marks_missing_pieces_down():
    r = running()
    r.delete("health:spark:metrics", "health:simulator")
    h = api.build_health(r, lambda: False)
    c = h["components"]
    assert (c["spark"]["status"], c["simulator"]["status"], c["database"]["status"], c["kafka"]["status"]) == \
        ("down", "down", "down", "down")
    assert h["status"] == "down"


def test_stale_stream_degrades():
    r = running()
    r.set("health:engine", json.dumps({"tick_at": now_iso(3), "freshness_s": 200}))
    assert api.build_health(r, lambda: True)["components"]["stream"]["status"] == "degraded"


def test_unknown_location_and_bad_inputs():
    with client(running()) as c:
        assert c.get("/api/locations/planet/X").status_code == 404
        assert c.get("/api/locations/city/NOPE").status_code == 404
        assert c.get("/api/map?mode=sunshine").status_code == 422
        assert c.get("/api/future?window=99").status_code == 422
        assert c.get("/api/search?q=a").status_code == 422


def test_websocket_sends_snapshot_then_relays_ticks():
    b = api.Broadcaster()
    with client(running(), b) as c:
        with c.websocket_connect("/ws") as ws:
            first = ws.receive_json()
            assert first["type"] == "snapshot" and first["overview"]["kpis"]["in_transit"] == 900
            c.portal.call(b.publish, json.dumps({"type": "tick", "overview": {}}))
            assert ws.receive_json()["type"] == "tick"


def test_slow_client_is_dropped_without_blocking_others():
    async def scenario():
        b = api.Broadcaster(maxsize=2)
        slow, fast = b.register(), b.register()
        for k in range(3):
            b.publish(f"m{k}")
            await fast.get()
        assert slow not in b.clients and fast in b.clients
        assert slow.get_nowait() is None    # close sentinel for the dropped client

    asyncio.run(scenario())


def test_ttl_cache_reuses_until_expiry():
    calls = []
    cache = api.TTLCache(ttl=60)
    assert cache.get("k", lambda: calls.append(1) or 1) == 1
    assert cache.get("k", lambda: calls.append(1) or 2) == 1
    assert len(calls) == 1
