"""Reading v2 validity rules - the single source of truth for both the
ingestor's tests and Spark's quarantine branch, which builds its column
expression from the same MEASUREMENTS order and RANGES."""
from datetime import datetime, timezone

# Order matters: the first failing field names the reject reason, and Spark
# walks the same tuple, so both paths report the same reason.
MEASUREMENTS = (
    "temperature_c",
    "humidity_pct",
    "rain_mmph",
    "wind_kmph",
    "gust_kmph",
    "visibility_m",
    "pressure_hpa",
    "rain_24h_mm",
)

RANGES = {
    "temperature_c": (-40, 60),
    "humidity_pct": (0, 100),
    "rain_mmph": (0, 500),
    "wind_kmph": (0, 300),
    "gust_kmph": (0, 400),
    "visibility_m": (0, 100000),
    "pressure_hpa": (850, 1100),
    "rain_24h_mm": (0, 2000),
}

# Producer clocks drift; two minutes absorbs that without letting a reading
# from a misconfigured clock push the watermark far ahead.
FUTURE_TOLERANCE_S = 120


def parse_ts(s):
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def fmt_ts(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def validate(reading, now=None):
    """Reject reason for a reading, or None when it is valid. A null
    measurement is a missing value, not an invalid one."""
    if not reading.get("station_id"):
        return "unparseable"
    try:
        event_time = parse_ts(reading["event_time"])
    except (KeyError, TypeError, ValueError):
        return "unparseable"

    for field in MEASUREMENTS:
        value = reading.get(field)
        if value is None:
            continue
        lo, hi = RANGES[field]
        if not isinstance(value, (int, float)) or not lo <= value <= hi:
            return f"{field}_out_of_range"

    now = now or datetime.now(timezone.utc)
    if (event_time - now).total_seconds() > FUTURE_TOLERANCE_S:
        return "future_timestamp"
    return None
