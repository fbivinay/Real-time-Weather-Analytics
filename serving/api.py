"""Read-only REST + WebSocket view of the engine's state in Redis.

Clients load /api/snapshot (or receive it as the first WebSocket message),
then apply the deltas the engine publishes on weatherops:events. One Redis
subscription per process fans out to every connected socket; a client that
stops reading is dropped rather than allowed to slow the others down.

Serves derived weather and synthetic logistics data only: no secrets, no
writes, no credentials - so CORS is open for GET.
"""
import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware

from weatherops.schema import parse_ts

log = logging.getLogger("api")

CHANNEL = "weatherops:events"
PING_S = 20
EMPTY_KPIS = {"routes_affected": {"linehaul": 0, "lastmile": 0}, "deliveries_active": 0,
              "deliveries_at_risk": 0, "hubs_affected": 0, "locations_high": 0, "active_incidents": 0}
RANK = {"ok": 0, "degraded": 1, "down": 2}


def _loads(value):
    return json.loads(value) if value else None


def _hash(r, key):
    return {k: json.loads(v) for k, v in r.hgetall(key).items()}


def _age(iso, now):
    try:
        return (now - parse_ts(iso.replace(".000", ""))).total_seconds()
    except (AttributeError, TypeError, ValueError):
        return None


def build_health(r, now=None):
    now = now or datetime.now(timezone.utc)
    components = {"api": {"status": "ok"}}
    try:
        r.ping()
        components["redis"] = {"status": "ok"}
    except Exception as exc:   # any client error means the store is unreachable
        components["redis"] = {"status": "down", "detail": str(exc)}
        return {"status": "down", "components": components}

    engine = _loads(r.get("health:engine"))
    if engine:
        age = _age(engine.get("tick_at"), now)
        status = "ok" if age is not None and age <= 30 else "degraded" if age is not None and age <= 120 else "down"
        components["engine"] = {"status": status, "tick_age_s": age, **engine}
        fresh = (engine.get("freshness_s") or {}).get("sensor")
        components["ingest"] = {"status": "ok" if fresh is not None and fresh <= 120 else
                                "degraded" if fresh is not None and fresh <= 600 else "down",
                                "freshness_s": engine.get("freshness_s")}
    else:
        components["engine"] = {"status": "down"}
        components["ingest"] = {"status": "down"}

    queries = {}
    for key in r.keys("health:spark:*"):
        if key.startswith("health:spark:terminated:"):
            continue
        digest = _loads(r.get(key)) or {}
        trigger = 60 if key.endswith("-s3") else 5
        age = _age(digest.get("at"), now)
        queries[digest.get("name") or key] = {
            "status": "ok" if age is not None and age <= 2 * trigger + 30 else "degraded", "age_s": age, **digest}
    if "features-kafka" not in queries:
        components["spark"] = {"status": "down", "queries": queries}
    else:
        worst = max((q["status"] for q in queries.values()), key=RANK.get)
        components["spark"] = {"status": worst, "queries": queries}

    overall = max((c["status"] for c in components.values()), key=RANK.get)
    return {"status": overall, "components": components}


def build_snapshot(r, now=None):
    return {
        "type": "snapshot",
        "mode": _loads(r.get("state:mode")),
        "kpis": _loads(r.get("state:kpis")) or dict(EMPTY_KPIS),
        "locations": _hash(r, "state:locations"),
        "routes": _hash(r, "state:routes"),
        "hubs": _hash(r, "state:hubs"),
        "incidents": list(_hash(r, "incidents:active").values()),
        "dq": _loads(r.get("dq:summary")) or {},
        "health": build_health(r, now),
    }


class Broadcaster:
    def __init__(self, maxsize=64):
        self.maxsize = maxsize
        self.clients = set()

    def register(self):
        q = asyncio.Queue(maxsize=self.maxsize)
        self.clients.add(q)
        return q

    def unregister(self, q):
        self.clients.discard(q)

    def publish(self, text):
        for q in list(self.clients):
            try:
                q.put_nowait(text)
            except asyncio.QueueFull:
                # This client stopped reading. Drop it; None tells its socket to close.
                self.clients.discard(q)
                while not q.empty():
                    q.get_nowait()
                q.put_nowait(None)


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


def create_app(r, redis_async=None, broadcaster=None):
    broadcaster = broadcaster or Broadcaster()

    @asynccontextmanager
    async def lifespan(_app):
        task = asyncio.create_task(relay(redis_async, broadcaster)) if redis_async is not None else None
        yield
        if task:
            task.cancel()

    app = FastAPI(title="WeatherOps API", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"])

    @app.get("/api/health")
    def health():
        return build_health(r)

    @app.get("/api/snapshot")
    def snapshot():
        return build_snapshot(r)

    @app.get("/api/incidents")
    def incidents(status: str = Query("active", pattern="^(active|history)$"), limit: int = 20):
        limit = max(1, min(limit, 200))
        if status == "active":
            rows = list(_hash(r, "incidents:active").values())[:limit]
        else:
            rows = [json.loads(v) for v in r.zrevrange("incidents:history", 0, limit - 1)]
        return {"status": status, "count": len(rows), "incidents": rows}

    @app.get("/api/stations/{station_id}")
    def station(station_id: str):
        location = _loads(r.hget("state:locations", station_id))
        if location is None:
            raise HTTPException(status_code=404, detail="unknown station or no data yet")
        series = [json.loads(p) for p in r.lrange(f"series:{station_id}", 0, -1)][::-1]
        return {"location": location, "series": series}

    @app.get("/api/model")
    def model():
        card = _loads(r.get("state:model"))
        if card is None:
            raise HTTPException(status_code=404, detail="no forecast model loaded")
        return card

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        await websocket.accept()
        q = broadcaster.register()
        try:
            await websocket.send_text(json.dumps(await run_in_threadpool(build_snapshot, r)))
            while True:
                try:
                    message = await asyncio.wait_for(q.get(), timeout=PING_S)
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
            broadcaster.unregister(q)

    return app


def _from_env():
    import redis
    import redis.asyncio

    host = os.environ.get("REDIS_HOST", "redis-master.weather-pipeline.svc.cluster.local")
    password = os.environ.get("REDIS_PASSWORD")
    return create_app(
        redis.Redis(host=host, port=6379, password=password, decode_responses=True, socket_timeout=5),
        redis.asyncio.Redis(host=host, port=6379, password=password, decode_responses=True),
    )


# Clients connect lazily, so importing this module (tests) opens no sockets.
app = _from_env()
