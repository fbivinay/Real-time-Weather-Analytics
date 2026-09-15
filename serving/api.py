"""Read-only HTTP view of what the consumer put in Redis."""
import json
import os

import redis
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

REDIS_HOST = os.environ.get("REDIS_HOST", "redis-master.weather-pipeline.svc.cluster.local")
REDIS_PASSWORD = os.environ.get("REDIS_PASSWORD")

app = FastAPI(title="Weather Pipeline API")

# The dashboard is served from Vercel, a different origin. Reads only, no
# credentials, no secrets in any response - so a permissive origin is the
# honest setting rather than a list that pretends to restrict anything.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

r = redis.Redis(host=REDIS_HOST, port=6379, password=REDIS_PASSWORD, decode_responses=True)


@app.get("/api/health")
def health():
    try:
        r.ping()
        return {"status": "ok", "redis": "up"}
    except redis.RedisError as exc:
        return {"status": "degraded", "redis": "down", "detail": str(exc)}


@app.get("/api/stations")
def stations():
    """Latest one-minute aggregate per station, newest window first."""
    keys = sorted(r.keys("station:*"))
    rows = [json.loads(v) for v in (r.mget(keys) if keys else []) if v]
    rows.sort(key=lambda x: x["station_id"])
    return {"count": len(rows), "stations": rows}


@app.get("/api/alerts")
def alerts(limit: int = 20):
    """Most recent alerts, newest first."""
    limit = max(1, min(limit, 50))
    rows = [json.loads(v) for v in r.lrange("alerts", 0, limit - 1)]
    return {"count": len(rows), "alerts": rows}


@app.get("/api/stats")
def stats():
    return {
        "records_processed": int(r.get("stats:records") or 0),
        "stations_tracked": len(r.keys("station:*")),
        "alerts_buffered": r.llen("alerts"),
    }
