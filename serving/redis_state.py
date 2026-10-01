"""Engine tick -> Redis: current state for the API's snapshot, a 7-day
incident history, and a pub/sub stream of deltas for WebSocket clients.

Only what changed since the previous tick is re-written and published, so a
quiet network costs a few bytes per tick rather than the whole map.
"""
import json
from datetime import timedelta

from weatherops.schema import parse_ts

CHANNEL = "weatherops:events"
HISTORY_DAYS = 7
SERIES_POINTS = 120   # per-station sparkline for the station detail view
HASHES = {"locations": "state:locations", "routes": "state:routes", "hubs": "state:hubs"}
# What changes tick to tick for a station already known to clients; names,
# kinds and coordinates go out once, with the station's first appearance.
DYNAMIC_LOCATION_FIELDS = ("assessment", "values", "verdict", "observed_at", "forecast")


def _dumps(obj):
    return json.dumps(obj, separators=(",", ":"), default=str)


def write_tick(r, result, previous, now):
    """Write one tick; returns the state to pass as `previous` next time."""
    mode = _dumps(result["mode"])
    # Identity is the data source, not the weather clock: observed_at and
    # speed move every tick and must not wipe the state.
    source = _dumps([(result["mode"] or {}).get("source"), (result["mode"] or {}).get("scenario")])
    mode_changed = previous.get("source") not in (None, source)
    pipe = r.pipeline(transaction=False)
    if mode_changed:
        pipe.delete(*HASHES.values(), "incidents:active")
        previous = {}
        pipe.publish(CHANNEL, _dumps({"type": "mode", "mode": result["mode"]}))
    pipe.set("state:mode", mode)

    nxt, deltas = {"source": source}, {}
    for name, key in HASHES.items():
        old = previous.get(name, {})
        cur = {k: _dumps(v) for k, v in result[name].items()}
        changed = {k: v for k, v in cur.items() if old.get(k) != v}
        removed = [k for k in old if k not in cur]
        if changed:
            pipe.hset(key, mapping=changed)
        if removed:
            pipe.hdel(key, *removed)
        nxt[name] = cur
        deltas[name] = {k: result[name][k] for k in changed}
        if name == "locations":
            deltas[name] = {k: v if k not in old else {f: v.get(f) for f in DYNAMIC_LOCATION_FIELDS}
                            for k, v in deltas[name].items()}

    for sid in deltas["locations"]:
        loc = result["locations"][sid]
        values = loc.get("values") or {}
        point = {"t": loc.get("observed_at"), "score": (loc.get("assessment") or {}).get("score"),
                 "rain": values.get("rain_avg"), "gust": values.get("gust_max"),
                 "temp": values.get("temp_avg"), "vis": values.get("visibility_min")}
        pipe.lpush(f"series:{sid}", _dumps(point))
        pipe.ltrim(f"series:{sid}", 0, SERIES_POINTS - 1)

    pipe.set("state:kpis", _dumps(result["kpis"]))
    pipe.set("dq:summary", _dumps(result["dq"]))
    pipe.set("health:engine", _dumps(result["health"]))

    active = {i["id"]: _dumps(i) for i in result["incidents"]}
    gone = [i for i in previous.get("incidents", ()) if i not in active]
    if active:
        pipe.hset("incidents:active", mapping=active)
    if gone:
        pipe.hdel("incidents:active", *gone)
    nxt["incidents"] = set(active)
    for event in result["events"]:
        if event["type"] == "resolved":
            inc = event["incident"]
            pipe.hdel("incidents:active", inc["id"])
            pipe.zadd("incidents:history", {_dumps(inc): parse_ts(inc["resolved_at"]).timestamp()})
    pipe.zremrangebyscore("incidents:history", "-inf", (now - timedelta(days=HISTORY_DAYS)).timestamp())

    pipe.publish(CHANNEL, _dumps({"type": "tick", "mode": result["mode"], "kpis": result["kpis"],
                                  **deltas, "removed": {n: [k for k in previous.get(n, {}) if k not in nxt[n]]
                                                        for n in HASHES},
                                  "incidents": result["incidents"], "dq": result["dq"],
                                  "health": result["health"]}))
    for event in result["events"]:
        pipe.publish(CHANNEL, _dumps({"type": "incident", "event": event["type"], "incident": event["incident"]}))
    pipe.execute()
    return nxt


def decision_records(result):
    """Audit trail for S3: every incident change, plus the periodic snapshot."""
    records = [{"record_type": "incident", "event": e["type"], **e["incident"]} for e in result["events"]]
    if result.get("snapshot"):
        records.append(result["snapshot"])
    return records
