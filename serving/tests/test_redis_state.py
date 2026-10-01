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
        "prealerts": [{"region": "CHE", "forecast_category": "high"}],
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


def test_changed_locations_append_to_a_bounded_series():
    r = FakeRedis()
    loc = {"station_id": "REF-CHE", "observed_at": "2026-11-20T08:00:00Z",
           "assessment": {"score": 40}, "values": {"rain_avg": 12.0, "gust_max": 40.0, "temp_avg": 27.0,
                                                    "visibility_min": 5000.0}}
    prev = {}
    for k in range(redis_state.SERIES_POINTS + 5):
        loc = dict(loc, observed_at=f"2026-11-20T08:{k % 60:02d}:00Z", assessment={"score": k})
        prev = redis_state.write_tick(r, result(locations={"REF-CHE": loc}), prev, NOW)
    series = [json.loads(p) for p in r.lrange("series:REF-CHE", 0, -1)]
    assert len(series) == redis_state.SERIES_POINTS
    assert series[0]["score"] == redis_state.SERIES_POINTS + 4      # newest first
    assert set(series[0]) == {"t", "score", "rain", "gust", "temp", "vis"}


def test_advancing_weather_clock_is_not_a_mode_change():
    r = FakeRedis()
    prev = redis_state.write_tick(r, result(), {}, NOW)
    later = {"source": "sim", "scenario": "storm-chennai", "observed_at": "2026-11-20T08:15:00Z", "speed": 31}
    redis_state.write_tick(r, result(mode=later), prev, NOW + timedelta(seconds=10))
    assert messages(r, "mode") == []
    assert messages(r, "tick")[-1]["locations"] == {}     # nothing re-sent
    assert json.loads(r.get("state:mode"))["observed_at"] == "2026-11-20T08:15:00Z"


def test_ticks_send_static_fields_only_for_new_locations():
    r = FakeRedis()
    full = {"station_id": "REF-CHE", "name": "Chennai", "lat": 13.08, "lon": 80.27, "kind": "reference",
            "city_id": "CHE", "hub_id": None, "verdict": None, "assessment": {"score": 10},
            "values": {"rain_avg": 1.0}, "observed_at": "a", "forecast": None}
    prev = redis_state.write_tick(r, result(locations={"REF-CHE": full}), {}, NOW)
    assert messages(r, "tick")[-1]["locations"]["REF-CHE"]["lat"] == 13.08       # new: full object
    changed = dict(full, assessment={"score": 60}, observed_at="b")
    redis_state.write_tick(r, result(locations={"REF-CHE": changed}), prev, NOW)
    delta = messages(r, "tick")[-1]["locations"]["REF-CHE"]
    assert delta["assessment"]["score"] == 60
    assert "lat" not in delta and "name" not in delta
    assert json.loads(r.hget("state:locations", "REF-CHE"))["lat"] == 13.08     # Redis keeps it all


def test_prealerts_are_stored_and_published():
    r = FakeRedis()
    redis_state.write_tick(r, result(), {}, NOW)
    assert json.loads(r.get("state:prealerts"))[0]["region"] == "CHE"
    assert messages(r, "tick")[-1]["prealerts"][0]["forecast_category"] == "high"
