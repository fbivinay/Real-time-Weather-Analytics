"""Open-Meteo client: stdlib HTTP plus pure parsers.

Free tier: non-commercial use, 10,000 calls/day, data under CC BY 4.0.
Each location counts as a call and requests over 10 variables are weighted,
so the regular 15-minute poll asks for 7 current variables + hourly rain.
"""
import json
import urllib.error
import urllib.request
from datetime import datetime, timezone
from urllib.parse import urlencode

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
HISTORICAL_URL = "https://historical-forecast-api.open-meteo.com/v1/forecast"

# Open-Meteo variable -> Conditions field
FIELDS = {
    "temperature_2m": "temperature_c",
    "relative_humidity_2m": "humidity_pct",
    "precipitation": "rain_mmph",
    "wind_speed_10m": "wind_kmph",
    "wind_gusts_10m": "gust_kmph",
    "visibility": "visibility_m",
    "pressure_msl": "pressure_hpa",
}
CURRENT_VARS = tuple(FIELDS)
HOURLY_VARS = tuple(FIELDS)


class OpenMeteoError(Exception):
    pass


def _parse_time(s):
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def _locations(payload, cities):
    if isinstance(payload, dict) and payload.get("error"):
        raise OpenMeteoError(payload.get("reason", "unknown error"))
    locations = payload if isinstance(payload, list) else [payload]
    if len(locations) != len(cities):
        raise OpenMeteoError(f"expected {len(cities)} locations, got {len(locations)}")
    return zip(cities, locations)


def _hourly_series(block):
    """Hourly block -> [(time, Conditions)] with a trailing 24 h rain sum.
    Hourly precipitation is the preceding hour's total, i.e. already mm/h."""
    times = [_parse_time(t) for t in block.get("time", [])]
    series = []
    for i, t in enumerate(times):
        cond = {field: (block[var][i] if var in block else None) for var, field in FIELDS.items()}
        window = [v for v in block.get("precipitation", [])[max(0, i - 23):i + 1] if v is not None]
        cond["rain_24h_mm"] = round(sum(window), 1) if window else None
        series.append((t, cond))
    return series


def parse_current(payload, cities):
    """-> ({city_id: (observed_at, Conditions)}, {city_id: [(time, Conditions)]})"""
    current, hourly = {}, {}
    for city, loc in _locations(payload, cities):
        block = loc["current"]
        cond = {field: block.get(var) for var, field in FIELDS.items()}
        if cond["rain_mmph"] is not None:
            # current precipitation covers the preceding interval (15 min)
            cond["rain_mmph"] = round(cond["rain_mmph"] * 3600 / block.get("interval", 900), 2)
        series = _hourly_series(loc.get("hourly", {}))
        cond["rain_24h_mm"] = series[-1][1]["rain_24h_mm"] if series else None
        current[city.id] = (_parse_time(block["time"]), cond)
        hourly[city.id] = series
    return current, hourly


def parse_hourly(payload, cities):
    return {city.id: _hourly_series(loc.get("hourly", {})) for city, loc in _locations(payload, cities)}


def _coords(cities):
    return {
        "latitude": ",".join(str(c.lat) for c in cities),
        "longitude": ",".join(str(c.lon) for c in cities),
    }


def current_url(cities, hourly_vars=("precipitation",), past_hours=25):
    return FORECAST_URL + "?" + urlencode({
        **_coords(cities),
        "current": ",".join(CURRENT_VARS),
        "hourly": ",".join(hourly_vars),
        "past_hours": past_hours,
        "forecast_hours": 0,
        "timezone": "UTC",
    })


def hourly_url(cities, start, end, url=HISTORICAL_URL):
    return url + "?" + urlencode({
        **_coords(cities),
        "hourly": ",".join(HOURLY_VARS),
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "timezone": "UTC",
    })


def _get_json(url, timeout):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        # Open-Meteo explains 400/429 in a JSON body; surface its reason.
        try:
            reason = json.load(exc).get("reason", exc.reason)
        except (ValueError, AttributeError):
            reason = exc.reason
        raise OpenMeteoError(f"HTTP {exc.code}: {reason}") from exc
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise OpenMeteoError(str(exc)) from exc


def fetch_current(cities, hourly_vars=("precipitation",), past_hours=25, timeout=20):
    return parse_current(_get_json(current_url(cities, hourly_vars, past_hours), timeout), cities)


def fetch_hourly(cities, start, end, url=HISTORICAL_URL, timeout=60):
    return parse_hourly(_get_json(hourly_url(cities, start, end, url), timeout), cities)
