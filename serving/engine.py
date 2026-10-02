"""WeatherOps engine: clean ShopFlow events in, operational picture out.

Consumes Spark's shopflow-clean (business events) and shopflow-metrics
(30 s windows), keeps live operations state (serving.ops), and every tick
writes the overview, impact, future-risk and health views to Redis (plus a
pub/sub delta for WebSocket clients) and persists orders, predictions,
weather risk and the live history roll-up to Postgres.
"""
import json
import logging
import os
import time
from collections import deque
from datetime import datetime, timedelta, timezone

from serving.ops import NET, WINDOWS, Ops, iso
from weatherops import company as co
from weatherops.events import parse_ts
from weatherops.rainfall import HourlyRain
from weatherops.replay import REPLAY_DAYS, REPLAY_START, SPEED

log = logging.getLogger("engine")
TICK_S = 5
RISK_EVERY_S = 20
CHANNEL = "weatherops:events"
FORECAST_H = 48


class Engine:
    def __init__(self, ops):
        self.ops = ops
        self.arrivals = deque()          # wall-clock seconds of consumed events (last 60 s)
        self.windows = deque(maxlen=40)  # Spark 30 s windows, network totals
        self._window_acc = {}
        self.last_risk = 0.0
        self.wrapped = False

    # ---- input ------------------------------------------------------------
    def on_event(self, e, wall=None):
        self.arrivals.append(wall if wall is not None else time.time())
        if self.ops.apply(e):
            self.wrapped = True

    def on_metric(self, m):
        """Sum Spark's per-city windows into one row per window."""
        key = m["window_start"]
        acc = self._window_acc.setdefault(key, {"window_start": key, "events": 0, "created": 0, "dispatched": 0,
                                                "completed": 0, "delays": 0})
        for f in ("events", "created", "dispatched", "completed", "delays"):
            acc[f] += m.get(f) or 0
        if len(self._window_acc) > 3:
            oldest = min(self._window_acc)
            self.windows.append(self._window_acc.pop(oldest))

    def events_per_s(self, now_wall):
        while self.arrivals and self.arrivals[0] < now_wall - 60:
            self.arrivals.popleft()
        return round(len(self.arrivals) / 60, 1)

    # ---- output -----------------------------------------------------------
    def forecast_grid(self):
        fc = self.ops.forecast_fn()
        start = self.ops.sim_now.replace(minute=0, second=0, microsecond=0)
        return {c: [{"p_rain": f["p_rain"], "mmph": f["mmph"],
                     "cls": max(f["probs"], key=f["probs"].get)}
                    for f in (fc(c, start + timedelta(hours=h), h) for h in range(FORECAST_H))]
                for c in NET.cities}

    def tick(self, now_wall=None, lag=None):
        now_wall = now_wall or time.time()
        ops = self.ops
        if ops.sim_now is None:
            return None
        recompute = now_wall - self.last_risk >= RISK_EVERY_S or not ops.cohort_risk or self.wrapped
        if recompute:
            ops.compute_risk()
            self.last_risk = now_wall
        impact = ops.impact()
        summary = ops.future_summary()
        live = ops.live()
        last = ops.last_event or {}
        emitted = last.get("emitted_at")
        freshness = round(now_wall - parse_ts(emitted).timestamp(), 1) if emitted else None
        stream = {
            "events_per_s": self.events_per_s(now_wall), "events_total": ops.events_seen,
            "last_event": last, "freshness_s": freshness, "consumer_lag": lag,
            "windows": list(self.windows)[-20:], "by_type": dict(ops.type_counts),
        }

        def top(group, n=6):
            rows = sorted(impact[group].values(), key=lambda x: (-x["score"], -x["orders"]))
            return rows[:n]

        overview = {
            "sim_time": iso(ops.sim_now), "kpis": live, "future": summary, "alerts": ops.alerts(impact, summary, live),
            "top": {"states": top("states"), "cities": top("cities"), "routes": top("routes", 8),
                    "warehouses": top("warehouses", 5)},
            "critical_orders": ops.critical_orders(12), "stream": stream,
            "replay": {"start": REPLAY_START, "days": REPLAY_DAYS, "speed": SPEED, "laps": ops.resets},
            "company": co.COMPANY,
        }
        return {
            "overview": overview, "impact": impact,
            "future": {"summary": summary, "timeline": ops.timeline(), "windows": list(WINDOWS)},
            "forecast": self.forecast_grid() if recompute else None,
            "weather": ops.weather, "recomputed": recompute,
            "health": {"tick_at": iso(datetime.now(timezone.utc)), "sim_time": iso(ops.sim_now),
                       "events_per_s": stream["events_per_s"], "events_total": ops.events_seen,
                       "freshness_s": freshness, "consumer_lag": lag, "orders_in_memory": len(ops.orders)},
        }


def write_redis(r, result):
    pipe = r.pipeline()
    pipe.set("state:overview", json.dumps(result["overview"]))
    pipe.set("state:impact", json.dumps(result["impact"]))
    pipe.set("state:future", json.dumps(result["future"]))
    pipe.set("state:weather", json.dumps(result["weather"]))
    if result["forecast"] is not None:
        pipe.set("state:forecast", json.dumps(result["forecast"]))
    pipe.set("health:engine", json.dumps(result["health"]), ex=120)
    pipe.publish(CHANNEL, json.dumps({"type": "tick", "overview": result["overview"],
                                      "impact": {k: result["impact"][k] for k in ("states", "cities", "routes")}}))
    pipe.execute()


def persist(store, ops, result):
    orders, items = ops.take_dirty()
    gone = ops.prune()
    rollup = ops.take_rollup()
    weather = {c: {**ops.weather[c], "score": result["impact"]["cities"][c]["score"]} for c in NET.cities}
    store.write(orders, items, gone, rollup, weather, ops.sim_now,
                ops.cohort_risk if result["recomputed"] else None)


def _lag(consumer):
    try:
        parts = consumer.assignment()
        ends = consumer.end_offsets(list(parts))
        return int(sum(max(0, ends[p] - (consumer.position(p) or 0)) for p in parts))
    except Exception:  # lag is diagnostics; never fail a tick on it
        return None


def main():
    import redis
    from kafka import KafkaConsumer

    from serving.store import Store, connect

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    bootstrap = os.environ.get("KAFKA_BOOTSTRAP", "kafka.weather-pipeline.svc.cluster.local:9092")
    r = redis.Redis(host=os.environ.get("REDIS_HOST", "redis-master.weather-pipeline.svc.cluster.local"),
                    port=6379, password=os.environ.get("REDIS_PASSWORD"), decode_responses=True)
    r.ping()
    store = Store(connect())
    coefs, hist = store.load_model()
    data = os.environ.get("DATA_DIR", "/app/data")
    engine = Engine(Ops(HourlyRain.load(os.path.join(data, "rain_hourly.csv.gz")), coefs, hist))
    # Rebuild from the retained stream (24 h covers a whole replay lap):
    # replaying the topic restores scheduled and in-flight orders after a
    # restart, so first forget the live days the replay will write again.
    store.reset_live()
    consumer = KafkaConsumer("shopflow-clean", "shopflow-metrics", bootstrap_servers=bootstrap,
                             group_id=None, auto_offset_reset="earliest", enable_auto_commit=False,
                             value_deserializer=lambda v: json.loads(v.decode("utf-8")), max_poll_records=5000)
    log.info("engine consuming shopflow-clean + shopflow-metrics from %s", bootstrap)
    next_tick, next_prune = time.monotonic(), time.monotonic()
    while True:
        for tp, msgs in consumer.poll(timeout_ms=500).items():
            for m in msgs:
                try:
                    if tp.topic == "shopflow-clean":
                        engine.on_event(m.value)
                    else:
                        engine.on_metric(m.value)
                except Exception:
                    # One malformed record must not stop the engine.
                    log.exception("skipping record %s@%s", tp.topic, m.offset)
        if time.monotonic() < next_tick:
            continue
        next_tick = time.monotonic() + TICK_S
        lag = _lag(consumer)
        if lag and lag > 20000:
            engine.ops.prune()  # catching up after a restart: keep memory flat, tick once caught up
            continue
        try:
            if engine.wrapped:
                store.reset_live()
            result = engine.tick(lag=lag)
            if result is None:
                continue
            engine.wrapped = False
            write_redis(r, result)
            persist(store, engine.ops, result)
            if time.monotonic() > next_prune:
                store.prune_events()
                next_prune = time.monotonic() + 600
        except Exception:
            log.exception("tick failed; retrying next tick")
            try:
                store.conn.rollback()
            except Exception:
                store = Store(connect())
            continue
        log.info("tick sim=%s orders=%d eps=%s lag=%s", result["health"]["sim_time"],
                 len(engine.ops.orders), result["health"]["events_per_s"], lag)


if __name__ == "__main__":
    main()
