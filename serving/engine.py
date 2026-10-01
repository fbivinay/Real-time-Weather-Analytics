"""WeatherOps risk engine: feature windows in, operational decisions out.

Every tick it judges each hub sensor (fault or weather?), scores risk at
every station it trusts, maps that onto routes, hubs and deliveries, opens
and closes incidents per city region, and attaches recommended actions.
The Engine class does no I/O; main() wires it to Kafka and Redis.
"""
import json
import logging
import statistics
from collections import Counter, defaultdict, deque
from datetime import timedelta

from weatherops import actions, anomaly, impact, risk
from weatherops.incidents import IncidentManager
from weatherops.schema import fmt_ts, parse_ts

log = logging.getLogger("engine")

KEEP_OBSERVED = timedelta(hours=4)
KEEP_WINDOWS = anomaly.HISTORY + 1
REGRESSION = timedelta(hours=1)
SNAPSHOT_EVERY = timedelta(seconds=60)
AFFECTED = ("high", "critical")
TIME_FIELDS = ("window_start", "window_end", "observed_from", "observed_to", "last_event_at")
VALUE_FIELDS = ("temp_avg", "humidity_avg", "rain_avg", "rain_max", "wind_avg", "gust_max",
                "visibility_min", "pressure_avg")
EMPTY_KPIS = {"routes_affected": {"linehaul": 0, "lastmile": 0}, "deliveries_active": 0,
              "deliveries_at_risk": 0, "hubs_affected": 0, "locations_high": 0, "active_incidents": 0}


def _percentile(values, q):
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[int(q * (len(ordered) - 1))], 1)


class Engine:
    def __init__(self, network, climatology=None, forecaster=None):
        self.network = network
        self.climatology = climatology or {}
        self.forecaster = forecaster
        self.impact_model = impact.ImpactModel(network)
        self.incidents = IncidentManager()
        self._spark = {"dropped_late": 0, "dropped_duplicates": 0}
        self._reset()

    def _reset(self):
        self.windows = defaultdict(dict)     # station -> {window_start: window}
        self.mode_key = None
        self.observed_at = None
        self._first = None
        self.quarantine = Counter()
        self._recent_quarantine = deque(maxlen=10)
        self.missing = 0
        self.delayed = 0
        self._last_seq = {}
        self._fresh = set()
        self._pending_latency = []
        self._latency = deque(maxlen=600)
        self._suspect_since = {}
        self._forecasts = {}
        self._last_snapshot = None
        self.incidents.reset()

    # ---- inputs ---------------------------------------------------------

    def ingest_window(self, raw):
        w = dict(raw)
        for field in TIME_FIELDS:
            if w.get(field):
                w[field] = parse_ts(w[field])
        sid = w.get("station_id")
        if sid not in self.network.stations:
            return
        key = (w.get("source"), w.get("scenario"))
        regressed = self.observed_at is not None and w["observed_to"] < self.observed_at - REGRESSION
        if self.mode_key is not None and (key != self.mode_key or regressed):
            log.info("mode %s -> %s%s: resetting state", self.mode_key, key, " (clock went back)" if regressed else "")
            self._reset()
        self.mode_key = key

        last = self._last_seq.get(sid)
        if last is not None and w["seq_min"] > last:
            self.missing += w["seq_min"] - last - 1
        # seq_min <= last: the ingestor restarted and counts from 0 again
        self.missing += max(0, (w["seq_max"] - w["seq_min"] + 1) - w["readings"])
        self._last_seq[sid] = w["seq_max"]
        self.delayed += w.get("delayed") or 0

        self.windows[sid][w["window_start"]] = w
        self.observed_at = w["observed_to"] if self.observed_at is None else max(self.observed_at, w["observed_to"])
        self._fresh.add(sid)
        if w.get("last_event_at"):
            self._pending_latency.append(w["last_event_at"])

    def ingest_quarantine(self, q):
        self.quarantine[q.get("reason") or "unknown"] += 1
        self._recent_quarantine.append({k: q.get(k) for k in ("reason", "station_id", "event_time")})

    def ingest_spark_counters(self, dropped_late, dropped_duplicates):
        self._spark = {"dropped_late": int(dropped_late or 0), "dropped_duplicates": int(dropped_duplicates or 0)}

    def set_forecasts(self, forecasts):
        """city_id -> predicted risk inputs at +60 min (from the forecaster)."""
        self._forecasts = forecasts

    # ---- tick -----------------------------------------------------------

    def _prune(self, observed):
        for sid, by_start in self.windows.items():
            ordered = sorted(by_start)
            keep = set(ordered[-KEEP_WINDOWS:])
            keep |= {s for s in ordered if by_start[s]["observed_to"] >= observed - KEEP_OBSERVED}
            for s in ordered:
                if s not in keep:
                    del by_start[s]

    def _dq(self, verdicts, now):
        suspect = []
        for sid, v in sorted(verdicts.items()):
            if v["status"] == "sensor_suspect":
                since = self._suspect_since.setdefault(sid, now)
                suspect.append({"station_id": sid, "reason": v["reason"], "field": v["field"], "since": fmt_ts(since)})
            else:
                self._suspect_since.pop(sid, None)
        return {
            "quarantine": dict(self.quarantine),
            "recent_quarantine": list(self._recent_quarantine),
            "dropped_late": self._spark["dropped_late"],
            "dropped_duplicates": self._spark["dropped_duplicates"],
            "missing_readings": self.missing,
            "delayed_readings": self.delayed,
            "suspect": suspect,
            "stale": sorted(sid for sid, v in verdicts.items() if v["status"] == "stale"),
        }

    def _health(self, series, now):
        def age(kind):
            ends = [ws[-1]["window_end"] for sid, ws in series.items() if self.network.stations[sid].kind == kind]
            return round((now - max(ends)).total_seconds(), 1) if ends else None

        return {"tick_at": fmt_ts(now), "latency_p50_s": _percentile(self._latency, 0.5),
                "latency_p95_s": _percentile(self._latency, 0.95),
                "freshness_s": {"reference": age("reference"), "sensor": age("sensor")}}

    def tick(self, now):
        self._latency.extend((now - t).total_seconds() for t in self._pending_latency)
        self._pending_latency = []
        if self.observed_at is None:
            return {"mode": None, "locations": {}, "routes": {}, "hubs": {}, "kpis": json.loads(json.dumps(EMPTY_KPIS)),
                    "events": [], "incidents": [], "dq": self._dq({}, now), "health": self._health({}, now),
                    "snapshot": None}

        observed = self.observed_at
        self._prune(observed)
        series = {sid: [by_start[s] for s in sorted(by_start)] for sid, by_start in self.windows.items() if by_start}

        verdicts = {}
        for hub_id, sensors in self.network.sensors_by_hub.items():
            group = {s.id: series[s.id] for s in sensors if s.id in series}
            if group:
                verdicts.update(anomaly.verdicts(group, now))

        rain_24h = {}
        for ref in self.network.references:
            values = [w["rain_24h"] for w in series.get(ref.id, []) if w.get("rain_24h") is not None]
            rain_24h[ref.city_id] = values[-1] if values else None

        month = str(observed.month)
        locations, scores = {}, {}
        for sid, ws in series.items():
            st = self.network.stations[sid]
            verdict = verdicts.get(sid) if st.kind == "sensor" else None
            assessment = None
            if verdict is None or verdict["status"] in ("ok", "weather_event"):
                inputs = risk.summarize(ws, observed, rain_24h=None if st.kind == "reference" else rain_24h.get(st.city_id))
                clim = (self.climatology.get(st.city_id) or {}).get(month)
                assessment = risk.assess(inputs, clim, self._forecasts.get(st.city_id))
                scores[sid] = assessment["score"]
            latest = ws[-1]
            locations[sid] = {
                "station_id": sid, "kind": st.kind, "name": st.name, "city_id": st.city_id, "hub_id": st.hub_id,
                "lat": st.lat, "lon": st.lon, "verdict": verdict, "assessment": assessment,
                "values": {f: latest.get(f) for f in VALUE_FIELDS},
                "observed_at": fmt_ts(latest["observed_to"]), "forecast": self._forecasts.get(st.city_id),
            }

        hub_scores = {}
        for hub_id, hub in self.network.hubs.items():
            trusted = [scores[s.id] for s in self.network.sensors_by_hub[hub_id] if s.id in scores]
            hub_scores[hub_id] = round(statistics.median(trusted)) if trusted else scores.get(f"REF-{hub.city_id}")
        out = self.impact_model.evaluate(scores, hub_scores, observed)

        affected_routes = defaultdict(list)
        for rid, r in out["routes"].items():
            if r["status"] in AFFECTED:
                affected_routes[r["region"]].append(rid)
        blocked = {rid for rids in affected_routes.values() for rid in rids
                   if self.network.routes[rid].kind == "linehaul"}
        hub_status = {h: v["status"] for h, v in out["hubs"].items()}

        by_region = defaultdict(list)
        for sid in scores:
            by_region[self.network.stations[sid].city_id].append(sid)
        regions = {}
        for city_id, sids in by_region.items():
            city = self.network.cities[city_id]
            worst = locations[max(sids, key=lambda s: scores[s])]["assessment"]
            routes = [{"id": rid, "kind": self.network.routes[rid].kind, "name": self.network.routes[rid].name,
                       "active": out["routes"][rid]["active"], "status": out["routes"][rid]["status"]}
                      for rid in affected_routes.get(city_id, [])]
            hubs = [{"id": h.id, "name": h.name, "status": hub_status[h.id], "score": hub_scores[h.id]}
                    for h in self.network.hubs.values() if h.city_id == city_id and hub_status[h.id] in AFFECTED]
            reroutes = {}
            for r in routes:
                if r["kind"] == "linehaul":
                    route = self.network.routes[r["id"]]
                    found = impact.reroute(self.network, blocked, *route.hub_ids, max_km=2 * route.length_km)
                    if found:
                        reroutes[r["id"]] = found
            impact_slice = {"routes": routes, "hubs": hubs, "reroutes": reroutes, "hub_status": hub_status,
                            "deliveries_at_risk": sum(r["active"] for r in routes)}
            regions[city_id] = {
                "name": city.name, "score": worst["score"], "category": worst["category"], "hazard": worst["hazard"],
                "fresh": any(s in self._fresh for s in sids), "routes": [r["id"] for r in routes],
                "hubs": [h["id"] for h in hubs], "deliveries_at_risk": impact_slice["deliveries_at_risk"],
                "actions": actions.recommend(city_id, city.name, worst, impact_slice, self.network),
            }
        events = self.incidents.update(regions, observed)
        active = self.incidents.active()

        kpis = dict(out["kpis"], active_incidents=len(active))
        if self._first is None:
            self._first = (now, observed)
        elapsed = (now - self._first[0]).total_seconds()
        speed = round((observed - self._first[1]).total_seconds() / elapsed) if elapsed >= 30 else None
        mode = {"source": self.mode_key[0], "scenario": self.mode_key[1], "observed_at": fmt_ts(observed),
                "speed": speed}

        snapshot = None
        if self._last_snapshot is None or now - self._last_snapshot >= SNAPSHOT_EVERY:
            self._last_snapshot = now
            snapshot = {"record_type": "snapshot", "observed_at": fmt_ts(observed), "mode": mode, "kpis": kpis,
                        "locations": [{"station_id": sid, "score": (loc["assessment"] or {}).get("score"),
                                       "category": (loc["assessment"] or {}).get("category"),
                                       "hazard": (loc["assessment"] or {}).get("hazard"),
                                       "verdict": (loc["verdict"] or {}).get("status")}
                                      for sid, loc in locations.items()]}

        self._fresh.clear()
        return {"mode": mode, "locations": locations, "routes": out["routes"], "hubs": out["hubs"], "kpis": kpis,
                "events": events, "incidents": active, "dq": self._dq(verdicts, now),
                "health": self._health(series, now), "snapshot": snapshot}


TICK_S = 10


def _lag(consumer):
    parts = consumer.assignment()
    if not parts:
        return None
    ends = consumer.end_offsets(list(parts))
    return sum(max(0, ends[tp] - consumer.position(tp)) for tp in parts)


def main():
    import os
    import time
    from datetime import datetime, timezone

    import redis
    from kafka import KafkaConsumer, KafkaProducer

    from serving.redis_state import HASHES, decision_records, write_tick
    from weatherops.network import NETWORK

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    bootstrap = os.environ.get("KAFKA_BOOTSTRAP", "kafka.weather-pipeline.svc.cluster.local:9092")
    r = redis.Redis(host=os.environ.get("REDIS_HOST", "redis-master.weather-pipeline.svc.cluster.local"),
                    port=6379, password=os.environ.get("REDIS_PASSWORD"), decode_responses=True)
    r.ping()
    # A restarted engine rebuilds state from new windows; clear what the last
    # run left so the dashboard never mixes the two.
    r.delete(*HASHES.values(), "incidents:active")

    engine = Engine(NETWORK, climatology=risk.load_climatology())
    consumer = KafkaConsumer("weather-features", "weather-quarantine", bootstrap_servers=bootstrap,
                             group_id="risk-engine", auto_offset_reset="latest",
                             value_deserializer=lambda v: json.loads(v.decode("utf-8")))
    producer = KafkaProducer(bootstrap_servers=bootstrap,
                             value_serializer=lambda v: json.dumps(v, default=str).encode("utf-8"))
    log.info("engine consuming weather-features + weather-quarantine from %s", bootstrap)

    previous, next_tick = {}, time.monotonic()
    while True:
        for tp, msgs in consumer.poll(timeout_ms=1000).items():
            handle = engine.ingest_window if tp.topic == "weather-features" else engine.ingest_quarantine
            for m in msgs:
                try:
                    handle(m.value)
                except Exception:
                    # One malformed record must not stop the engine; Kafka keeps it.
                    log.exception("skipping record %s@%s", tp.topic, m.offset)
        if time.monotonic() < next_tick:
            continue
        next_tick = time.monotonic() + TICK_S
        try:
            engine.ingest_spark_counters(r.get("dq:dropped_late"), r.get("dq:dropped_duplicates"))
            now = datetime.now(timezone.utc)
            result = engine.tick(now)
            result["health"]["consumer_lag"] = _lag(consumer)
            previous = write_tick(r, result, previous, now)
        except redis.RedisError:
            log.exception("Redis unavailable; state catches up on the next tick")
            continue
        for record in decision_records(result):
            producer.send("weather-decisions", record)
        producer.flush(timeout=5)
        log.info("tick observed=%s incidents=%d routes_affected=%s",
                 (result["mode"] or {}).get("observed_at"), len(result["incidents"]),
                 result["kpis"]["routes_affected"])


if __name__ == "__main__":
    main()
