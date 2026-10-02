"""ShopFlow event contract - one envelope for every stream event, and its
validity rules. Imported by the simulator, the engine and Spark's quarantine
branch (Python 3.8 in the Spark image: keep this file 3.8-compatible).

Envelope (JSON):
  event_id   unique id; a repeated id is a duplicate
  type       one of TYPES
  sim_time   simulated business time (UTC ISO-8601)
  emitted_at wall-clock time the simulator sent it
  order_id, city_id, route_id   when the event concerns one
  payload    JSON-encoded string, type-specific
"""
import json
import uuid
from datetime import datetime, timezone

TYPES = (
    "ORDER_CREATED", "ORDER_ASSIGNED", "VEHICLE_DISPATCHED", "VEHICLE_MOVEMENT", "HUB_ARRIVAL",
    "DELIVERY_DELAY", "DELIVERY_COMPLETED", "WEATHER_EVENT", "RISK_UPDATE",
)
NEEDS_ORDER = ("ORDER_CREATED", "ORDER_ASSIGNED", "DELIVERY_COMPLETED")
NEEDS_CITY = ("WEATHER_EVENT",)
# Producer clocks drift; two minutes absorbs that without letting a bad clock
# push Spark's watermark far ahead.
FUTURE_TOLERANCE_S = 120


def parse_ts(s):
    # Python 3.8's fromisoformat() rejects "Z".
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def fmt_ts(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def make(type_, sim_time, payload, order_id=None, city_id=None, route_id=None, now=None):
    return {
        "event_id": uuid.uuid4().hex[:20],
        "type": type_,
        "sim_time": fmt_ts(sim_time),
        "emitted_at": fmt_ts(now or datetime.now(timezone.utc)),
        "order_id": order_id,
        "city_id": city_id,
        "route_id": route_id,
        "payload": json.dumps(payload, separators=(",", ":")),
    }


def validate(event, now=None):
    """Reject reason, or None when valid. Same rule order as Spark's reject_reason()."""
    if not event.get("event_id") or not event.get("sim_time") or not event.get("emitted_at"):
        return "unparseable"
    try:
        emitted = parse_ts(event["emitted_at"])
        parse_ts(event["sim_time"])
    except (TypeError, ValueError, AttributeError):
        return "unparseable"
    if event.get("type") not in TYPES:
        return "unknown_type"
    if event["type"] in NEEDS_ORDER and not event.get("order_id"):
        return "missing_order_id"
    if event["type"] in NEEDS_CITY and not event.get("city_id"):
        return "missing_city_id"
    now = now or datetime.now(timezone.utc)
    if (emitted - now).total_seconds() > FUTURE_TOLERANCE_S:
        return "future_timestamp"
    return None
