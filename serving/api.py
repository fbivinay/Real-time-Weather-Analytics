"""WeatherOps API: live state from Redis, drill-downs, future risk, history,
scenarios and search from Postgres, and a WebSocket that relays the engine's
ticks. Synthetic demo data only - no secrets, no personal data; the one write
is a scenario run. CORS is open for GET and POST.
"""
import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Body, FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware

from serving import queries as q
from weatherops import company as co
from weatherops import scenario
from weatherops.events import parse_ts
from weatherops.rainfall import forecast

log = logging.getLogger("api")

CHANNEL = "weatherops:events"
PING_S = 20
RANK = {"ok": 0, "degraded": 1, "down": 2}
KINDS = ("state", "city", "warehouse", "hub", "route")
CACHE_S = 60


def _loads(value):
    return json.loads(value) if value else None


def _age(iso, now):
    try:
        return round((now - parse_ts(iso.replace(".000", ""))).total_seconds(), 1)
    except (AttributeError, TypeError, ValueError):
        return None


def _status(age, ok, degraded):
    return "down" if age is None else "ok" if age <= ok else "degraded" if age <= degraded else "down"


def build_health(r, db_check, now=None):
    now = now or datetime.now(timezone.utc)
    c = {"api": {"status": "ok"}}
    try:
        r.ping()
        c["redis"] = {"status": "ok"}
    except Exception as exc:   # any client error means the store is unreachable
        c["redis"] = {"status": "down", "detail": str(exc)}
        c["database"] = {"status": "down" if not db_check() else "ok"}
        return {"status": "down", "components": c}
    c["database"] = {"status": "ok" if db_check() else "down"}

    sim = _loads(r.get("health:simulator"))
    sim_age = _age(sim.get("at"), now) if sim else None
    c["simulator"] = {"status": _status(sim_age, 30, 120), "age_s": sim_age, **(sim or {})}

    queries = {}
    for key in r.keys("health:spark:*"):
        if key.startswith("health:spark:terminated:"):
            continue
        d = _loads(r.get(key)) or {}
        age = _age(d.get("at"), now)
        trigger = 60 if (d.get("name") or "").endswith("-s3") else 5
        queries[d.get("name") or key] = {"status": _status(age, 2 * trigger + 30, 300), "age_s": age, **d}
    spark_ok = "clean" in queries and "metrics" in queries
    c["spark"] = {"status": max((v["status"] for v in queries.values()), key=RANK.get) if spark_ok else "down",
                  "queries": queries}

    eng = _loads(r.get("health:engine"))
    eng_age = _age(eng.get("tick_at"), now) if eng else None
    c["engine"] = {"status": _status(eng_age, 30, 120), "tick_age_s": eng_age, **(eng or {})}
    fresh = (eng or {}).get("freshness_s")
    c["stream"] = {"status": _status(fresh, 60, 300), "freshness_s": fresh,
                   "events_per_s": (eng or {}).get("events_per_s"), "consumer_lag": (eng or {}).get("consumer_lag")}
    # Kafka has no health endpoint of its own here: it is healthy when events
    # go in (simulator) and come out (Spark -> engine) on time.
    c["kafka"] = {"status": max(c["simulator"]["status"], c["stream"]["status"], key=RANK.get)}
    overall = max((v["status"] for v in c.values()), key=RANK.get)
    return {"status": overall, "components": c}


class Broadcaster:
    def __init__(self, maxsize=32):
        self.maxsize = maxsize
        self.clients = set()

    def register(self):
        queue = asyncio.Queue(maxsize=self.maxsize)
        self.clients.add(queue)
        return queue

    def unregister(self, queue):
        self.clients.discard(queue)

    def publish(self, text):
        for queue in list(self.clients):
            try:
                queue.put_nowait(text)
            except asyncio.QueueFull:
                # This client stopped reading. Drop it; None tells its socket to close.
                self.clients.discard(queue)
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait(None)


async def relay(redis_async, broadcaster):
    """Redis pub/sub -> every connected WebSocket, reconnecting on failure."""
    while True:
        try:
            pubsub = redis_async.pubsub()
            await pubsub.subscribe(CHANNEL)
            async for message in pubsub.listen():
                if message.get("type") == "message":
                    broadcaster.publish(message["data"])
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("pub/sub relay failed; reconnecting")
            await asyncio.sleep(2)


class TTLCache:
    def __init__(self, ttl):
        self.ttl, self.data = ttl, {}

    def get(self, key, fn):
        hit = self.data.get(key)
        if hit and time.monotonic() - hit[0] < self.ttl:
            return hit[1]
        value = fn()
        self.data[key] = (time.monotonic(), value)
        if len(self.data) > 256:
            self.data.pop(next(iter(self.data)))
        return value


def create_app(r, connect, hourly, redis_async=None, broadcaster=None):
    """connect(): a context-managed psycopg connection; hourly: HourlyRain."""
    broadcaster = broadcaster or Broadcaster()
    cache = TTLCache(CACHE_S)

    @asynccontextmanager
    async def lifespan(_app):
        task = asyncio.create_task(relay(redis_async, broadcaster)) if redis_async is not None else None
        yield
        if task:
            task.cancel()

    app = FastAPI(title="WeatherOps API", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST"], allow_headers=["*"])

    def db(fn, *args):
        with connect() as conn:
            return fn(conn, *args)

    def db_check():
        try:
            return db(lambda conn: q.one(conn, "SELECT 1 AS ok"))["ok"] == 1
        except Exception:
            return False

    def state(key):
        value = _loads(r.get(key))
        if value is None:
            raise HTTPException(503, "the engine has not produced this view yet")
        return value

    def sim_now():
        return parse_ts(state("state:overview")["sim_time"])

    @app.get("/api/health")
    def health():
        return build_health(r, db_check)

    @app.get("/api/overview")
    def overview():
        return state("state:overview")

    @app.get("/api/impact")
    def impact():
        return state("state:impact")

    @app.get("/api/map")
    def map_(mode: str = Query("impact", pattern="^(rainfall|impact)$"), month: int = Query(None, ge=1, le=12)):
        if mode == "rainfall":
            month = month or sim_now().month
            return cache.get(("rain", month), lambda: {"mode": mode, **db(q.rainfall_map, month)})
        live = state("state:impact")
        structural = {s["id"]: s for s in cache.get("structural", lambda: db(q.structural_impact))}
        worst = max((s["cost_per_order"] or 0 for s in structural.values()), default=1) or 1
        cities = {}
        for cid, c in live["cities"].items():
            s = structural.get(cid, {})
            hist_score = 100 * (s.get("cost_per_order") or 0) / worst
            cities[cid] = {**c, "historical_score": round(hist_score, 1),
                           "impact_index": round(0.6 * c["score"] + 0.4 * hist_score, 1),
                           "affected_share": s.get("affected_share"), "breach_rate": s.get("breach_rate"),
                           "delay_per_order_min": s.get("delay_per_order_min")}
        states = {}
        for sid, st in live["states"].items():
            members = [c for c in cities.values() if c["state"] == sid]
            n = sum(c["orders"] for c in members) or len(members) or 1
            weight = (lambda c: c["orders"]) if sum(c["orders"] for c in members) else (lambda c: 1)
            states[sid] = {**st, "impact_index": round(sum(c["impact_index"] * weight(c) for c in members) / n, 1),
                           "historical_score": round(sum(c["historical_score"] * weight(c) for c in members) / n, 1)}
        return {"mode": mode, "cities": cities, "states": states, "routes": live["routes"],
                "warehouses": live["warehouses"], "hubs": live["hubs"]}

    @app.get("/api/locations/{kind}/{id_}")
    def location(kind: str, id_: str):
        if kind not in KINDS:
            raise HTTPException(404, "unknown location kind")
        groups = {"state": "states", "city": "cities", "warehouse": "warehouses", "hub": "hubs", "route": "routes"}
        live = state("state:impact")[groups[kind]].get(id_)
        net = co.NETWORK
        known = {"state": set(net.states), "city": net.cities, "warehouse": net.warehouses, "hub": net.hubs,
                 "route": net.routes}[kind]
        if id_ not in known:
            raise HTTPException(404, f"unknown {kind}")
        impact_all = state("state:impact")
        if kind == "state":
            children = [impact_all["cities"][c.id] for c in net.cities.values() if c.state == id_]
        elif kind == "city":
            children = [impact_all["routes"].get(r.id, {"id": r.id, "code": r.code, "orders": 0, "score": 0})
                        for r in net.routes_to[id_]]
        elif kind in ("warehouse", "hub"):
            field = "warehouse_id" if kind == "warehouse" else "hub_id"
            children = [impact_all["routes"].get(r.id, {"id": r.id, "code": r.code, "orders": 0, "score": 0})
                        for r in net.routes.values() if getattr(r, field) == id_]
        else:
            children = db(q.route_orders, id_)
        out = {"kind": kind, "id": id_, "live": live, "children": children,
               "history": cache.get(("loc", kind, id_), lambda: db(q.location_history, kind, id_))}
        if kind == "city":
            out["climatology"] = cache.get(("clim", id_), lambda: db(q.climatology_months, id_))
            out["weather"] = (_loads(r.get("state:weather")) or {}).get(id_)
            out["forecast"] = (_loads(r.get("state:forecast")) or {}).get(id_)
        if kind == "route":
            route = net.routes[id_]
            out["route"] = {"code": route.code, "distance_km": route.distance_km, "sensitivity": route.sensitivity,
                            "normal_eta_h": round(co.normal_eta_h(route), 2), "path": list(route.cities),
                            "warehouse_id": route.warehouse_id, "hub_id": route.hub_id,
                            "eta_by_class_h": {c: round(co.normal_eta_h(route) * (1 + (m - 1) * route.sensitivity), 2)
                                               for c, m in scenario.delay.MULTIPLIER.items()}}
        return out

    @app.get("/api/future")
    def future(window: int = Query(24, ge=1, le=48), sort: str = "score", page: int = Query(1, ge=1),
               size: int = Query(25, ge=5, le=100), category: str = None, city: str = None, route: str = None,
               warehouse: str = None, tier: str = None, state_: str = Query(None, alias="state"), hub: str = None,
               search: str = Query(None, alias="q")):
        filters = {"category": category, "city": city, "route": route, "warehouse": warehouse, "tier": tier,
                   "state": state_, "hub": hub, "q": search}
        table = db(q.future_orders, sim_now(), window, filters, sort, page, size)
        fut = state("state:future")
        return {"window": window, "summary": fut["summary"].get(str(window)), "table": table}

    @app.get("/api/future/timeline")
    def timeline():
        return state("state:future")

    @app.get("/api/orders/{order_id}")
    def order(order_id: str):
        detail = db(q.order_detail, order_id)
        if not detail:
            raise HTTPException(404, "order not found (delivered orders are kept for two simulated days)")
        return detail

    @app.post("/api/scenario")
    def run_scenario(body: dict = Body(...)):
        window = int(min(48, max(1, body.get("window_h", 24))))
        levers = {
            "dispatch_shift_h": float(min(8, max(-4, body.get("dispatch_shift_h", 0) or 0))),
            "reroute": bool(body.get("reroute")),
            "add_hub": body.get("add_hub") if body.get("add_hub") in co.NETWORK.cities else None,
            "rain_scale": float(min(2, max(0.5, body.get("rain_scale", 1) or 1))),
        }
        now = sim_now()
        coefs = cache.get("coefs", lambda: db(lambda c: q.one(c, "SELECT coefs FROM model_coefficients "
                                                                 "WHERE name = 'sla_breach'"))["coefs"])
        cohorts = db(q.scenario_cohorts, now, window)

        def fc(city_id, when, lead_h):
            return forecast(hourly, city_id, when.replace(minute=0, second=0, microsecond=0), max(0.0, lead_h))
        current = scenario.evaluate(cohorts, now, fc, coefs, window_h=window)
        simulated = scenario.evaluate(cohorts, now, fc, coefs, levers, window_h=window)
        run_id = db(q.save_scenario, now, window, levers, current, simulated)
        delta = {k: simulated[k] - current[k] for k in ("late", "sla_breaches", "exposed", "cost_inr")}
        delta["avg_delay_min"] = round(simulated["avg_delay_min"] - current["avg_delay_min"], 1)
        return {"run_id": run_id, "window_h": window, "levers": levers, "current": current,
                "simulated": simulated, "delta": delta, "label": "Simulated impact / Estimated outcome"}

    @app.get("/api/history")
    def history(year: int = None, month: int = None, state_: str = Query(None, alias="state"), city: str = None,
                warehouse: str = None, hub: str = None, route: str = None, category: str = None,
                severity: str = None):
        filters = {"year": year, "month": month, "state": state_, "city": city, "warehouse": warehouse, "hub": hub,
                   "route": route, "category": category, "severity": severity}
        key = tuple(sorted((k, v) for k, v in filters.items() if v not in (None, "")))
        return cache.get(("hist", key), lambda: {"filters": dict(key), **db(q.history, filters)})

    @app.get("/api/history/options")
    def history_options():
        return cache.get("options", lambda: db(q.history_options))

    @app.get("/api/search")
    def search_(text: str = Query(..., alias="q", min_length=2, max_length=40)):
        return db(q.search, text.strip())

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        await websocket.accept()
        queue = broadcaster.register()
        try:
            snap = await run_in_threadpool(lambda: {"type": "snapshot", "overview": _loads(r.get("state:overview")),
                                                    "impact": _loads(r.get("state:impact"))})
            await websocket.send_text(json.dumps(snap))
            while True:
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=PING_S)
                except asyncio.TimeoutError:
                    await websocket.send_text('{"type":"ping"}')
                    continue
                if message is None:
                    await websocket.close(code=1013)   # try again later: too slow
                    break
                await websocket.send_text(message)
        except WebSocketDisconnect:
            pass
        finally:
            broadcaster.unregister(queue)

    return app


def _from_env():
    import redis
    import redis.asyncio

    from serving.store import connect
    from weatherops.rainfall import HourlyRain

    host = os.environ.get("REDIS_HOST", "redis-master.weather-pipeline.svc.cluster.local")
    password = os.environ.get("REDIS_PASSWORD")
    data = os.environ.get("DATA_DIR", "/app/data")
    return create_app(
        redis.Redis(host=host, port=6379, password=password, decode_responses=True, socket_timeout=5),
        lambda: connect(autocommit=True),
        HourlyRain.load(os.path.join(data, "rain_hourly.csv.gz")),
        redis.asyncio.Redis(host=host, port=6379, password=password, decode_responses=True),
    )


# Built lazily by uvicorn (`serving.api:app` via factory) so tests open no sockets.
def app():
    return _from_env()
