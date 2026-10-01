import json
from datetime import datetime, timedelta, timezone

from serving import redis_state
from serving.tests.fakes import FakeRedis

NOW = datetime(2026, 11, 20, 8, 0, tzinfo=timezone.utc)


def result(locations=None, routes=None, incidents=None, events=None, mode=None, snapshot=None):
    return {
        "mode": mode or {"source": "sim", "scenario": "storm-chennai", "observed_at": "2026-11-20T08:00:00Z", "speed": 30},
        "locations": locations if locations is not None else {"REF-CHE": {"station_id": "REF-CHE", "assessment": {"score": 10}}},
        "routes": routes if routes is not None else {"LM-CHE-1": {"status": "low"}},
        "hubs": {"CHE": {"status": "low", "score": 3}},
        "kpis": {"active_incidents": 0},
        "events": events or [],
        "incidents": incidents or [],
        "dq": {"suspect": []},
        "health": {"tick_at": "2026-11-20T08:00:00Z"},
        "snapshot": snapshot,
    }


def messages(r, kind):
    return [json.loads(m) for _, m in r.published if json.loads(m)["type"] == kind]


def test_first_tick_publishes_everything():
    r = FakeRedis()
    redis_state.write_tick(r, result(), {}, NOW)
    [tick] = messages(r, "tick")
    assert set(tick["locations"]) == {"REF-CHE"}
    assert set(tick["routes"]) == {"LM-CHE-1"}
    assert json.loads(r.hget("state:locations", "REF-CHE"))["assessment"]["score"] == 10
    assert json.loads(r.get("state:mode"))["scenario"] == "storm-chennai"


def test_unchanged_tick_publishes_no_location_or_route_deltas():
    r = FakeRedis()
    prev = redis_state.write_tick(r, result(), {}, NOW)
    redis_state.write_tick(r, result(), prev, NOW + timedelta(seconds=10))
    tick = messages(r, "tick")[-1]
    assert tick["locations"] == {} and tick["routes"] == {}
    assert tick["kpis"] == {"active_incidents": 0}


def test_resolved_incident_moves_from_active_to_history():
    r = FakeRedis()
    inc = {"id": "INC-CHE-1", "region": "CHE", "status": "open", "resolved_at": None}
    prev = redis_state.write_tick(r, result(incidents=[inc], events=[{"type": "opened", "incident": inc}]), {}, NOW)
    assert r.hget("incidents:active", "INC-CHE-1")
    done = dict(inc, status="resolved", resolved_at="2026-11-20T08:20:00Z")
    redis_state.write_tick(r, result(events=[{"type": "resolved", "incident": done}]), prev, NOW + timedelta(minutes=20))
    assert r.hgetall("incidents:active") == {}
    assert json.loads(r.zrevrange("incidents:history", 0, -1)[0])["id"] == "INC-CHE-1"
    assert [m["event"] for m in messages(r, "incident")] == ["opened", "resolved"]


def test_history_older_than_seven_days_is_trimmed():
    r = FakeRedis()
    r.zadd("incidents:history", {json.dumps({"id": "old"}): (NOW - timedelta(days=8)).timestamp()})
    redis_state.write_tick(r, result(), {}, NOW)
    assert r.zrevrange("incidents:history", 0, -1) == []


def test_mode_change_clears_state_and_announces_it():
    r = FakeRedis()
    prev = redis_state.write_tick(r, result(), {}, NOW)
    other = {"source": "replay", "scenario": "michaung-2023", "observed_at": "2023-12-03T00:00:00Z", "speed": 120}
    redis_state.write_tick(r, result(locations={"REF-NLR": {"station_id": "REF-NLR"}}, mode=other), prev, NOW)
    assert set(r.hgetall("state:locations")) == {"REF-NLR"}
    assert messages(r, "mode")[-1]["mode"]["scenario"] == "michaung-2023"


def test_decision_records_cover_incident_events_and_snapshots():
    inc = {"id": "INC-CHE-1", "region": "CHE"}
    snap = {"record_type": "snapshot", "observed_at": "x", "kpis": {}, "locations": []}
    recs = redis_state.decision_records(result(events=[{"type": "opened", "incident": inc}], snapshot=snap))
    assert recs[0] == {"record_type": "incident", "event": "opened", **inc}
    assert recs[1] == snap
