import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from serving import api
from serving.tests.fakes import FakeRedis


def now_iso(delta_s=0):
    return (datetime.now(timezone.utc) - timedelta(seconds=delta_s)).strftime("%Y-%m-%dT%H:%M:%SZ")


def populated():
    r = FakeRedis()
    r.set("state:mode", json.dumps({"source": "sim", "scenario": "storm-chennai",
                                    "observed_at": "2026-11-20T08:40:00Z", "speed": 30}))
    r.set("state:kpis", json.dumps({"active_incidents": 1, "deliveries_at_risk": 263}))
    r.hset("state:locations", mapping={"REF-CHE": json.dumps({"station_id": "REF-CHE", "assessment": {"score": 73}})})
    r.hset("state:routes", mapping={"LM-CHE-1": json.dumps({"status": "high"})})
    r.hset("state:hubs", mapping={"CHE": json.dumps({"status": "high"})})
    r.hset("incidents:active", mapping={"INC-1": json.dumps({"id": "INC-1", "region": "CHE"})})
    r.zadd("incidents:history", {json.dumps({"id": "OLD-1"}): 100.0, json.dumps({"id": "OLD-2"}): 200.0})
    r.set("dq:summary", json.dumps({"suspect": []}))
    r.set("health:engine", json.dumps({"tick_at": now_iso(5), "latency_p50_s": 47.5, "freshness_s": {"sensor": 40}}))
    r.set("health:spark:features-kafka", json.dumps({"name": "features-kafka", "at": now_iso(3)}))
    r.lpush("series:REF-CHE", json.dumps({"t": "a", "score": 70}), json.dumps({"t": "b", "score": 73}))
    return r


def client(r, broadcaster=None):
    return TestClient(api.create_app(r, broadcaster=broadcaster or api.Broadcaster()))


def test_empty_redis_gives_a_valid_empty_snapshot():
    with client(FakeRedis()) as c:
        snap = c.get("/api/snapshot").json()
    assert snap["type"] == "snapshot"
    assert snap["mode"] is None
    assert snap["locations"] == {} and snap["routes"] == {} and snap["incidents"] == []
    assert snap["kpis"]["active_incidents"] == 0


def test_health_without_an_engine_is_down():
    with client(FakeRedis()) as c:
        health = c.get("/api/health").json()
    assert health["components"]["engine"]["status"] == "down"
    assert health["status"] == "down"


def test_health_of_a_running_pipeline_is_ok():
    with client(populated()) as c:
        health = c.get("/api/health").json()
    assert health["components"]["engine"]["status"] == "ok"
    assert health["components"]["spark"]["status"] == "ok"
    assert health["components"]["redis"]["status"] == "ok"


def test_snapshot_carries_state():
    with client(populated()) as c:
        snap = c.get("/api/snapshot").json()
    assert snap["mode"]["scenario"] == "storm-chennai"
    assert snap["locations"]["REF-CHE"]["assessment"]["score"] == 73
    assert snap["incidents"][0]["id"] == "INC-1"
    assert snap["kpis"]["deliveries_at_risk"] == 263


def test_incident_history_is_newest_first():
    with client(populated()) as c:
        history = c.get("/api/incidents?status=history&limit=5").json()
        active = c.get("/api/incidents").json()
    assert [i["id"] for i in history["incidents"]] == ["OLD-2", "OLD-1"]
    assert [i["id"] for i in active["incidents"]] == ["INC-1"]


def test_station_detail_and_unknown_station():
    with client(populated()) as c:
        detail = c.get("/api/stations/REF-CHE").json()
        missing = c.get("/api/stations/NOPE")
    assert detail["location"]["station_id"] == "REF-CHE"
    assert [p["score"] for p in detail["series"]] == [70, 73]    # oldest first for charts
    assert missing.status_code == 404


def test_model_card_404_until_the_engine_publishes_one():
    r = populated()
    with client(r) as c:
        assert c.get("/api/model").status_code == 404
        r.set("state:model", json.dumps({"version": "v1"}))
        assert c.get("/api/model").json()["version"] == "v1"


def test_websocket_sends_snapshot_then_relays_published_messages():
    b = api.Broadcaster()
    with client(populated(), b) as c:
        with c.websocket_connect("/ws") as ws:
            assert ws.receive_json()["type"] == "snapshot"
            c.portal.call(b.publish, json.dumps({"type": "tick", "kpis": {}}))
            assert ws.receive_json()["type"] == "tick"


def test_slow_client_is_dropped_without_blocking_others():
    async def scenario():
        b = api.Broadcaster(maxsize=2)
        slow, fast = b.register(), b.register()
        for k in range(3):
            b.publish(f"m{k}")
            await fast.get()
        assert slow not in b.clients
        assert fast in b.clients
        b.publish("after")
        assert await fast.get() == "after"
        assert slow.get_nowait() is None    # close sentinel for the dropped client

    asyncio.run(scenario())


def test_snapshot_includes_engine_health_so_metrics_show_on_load():
    with client(populated()) as c:
        snap = c.get("/api/snapshot").json()
    assert snap["engine"]["latency_p50_s"] == 47.5
