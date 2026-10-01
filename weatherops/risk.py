"""0-100 weather risk for logistics operations.

Each hazard gets a 0-1 sub-score by linear interpolation between anchors
taken from IMD categories (heavy rain, cyclone wind classes, heatwave,
dense fog). Sub-scores combine as a probabilistic OR, so two moderate
hazards compound: rain 0.5 and wind 0.5 make 0.75. Conditions beyond the
city's own 95th percentile for the month score 15% higher (infrastructure
is built for what is normal there), and a forecast can raise today's score
early. All anchors live in ANCHORS - the calibration knob.
"""
import math
from datetime import timedelta

ANCHORS = {
    "rain_intensity": ((2.5, 0.0), (7.5, 0.3), (15, 0.5), (30, 0.775), (50, 1.0)),   # mm/h
    "rain_accum": ((20, 0.0), (70, 0.5), (100, 0.775), (150, 1.0)),                 # mm
    "gust": ((40, 0.0), (50, 0.25), (62, 0.5), (89, 0.775), (118, 1.0)),            # km/h
    "heat": ((38, 0.0), (41, 0.3), (44, 0.5), (48, 0.775), (52, 1.0)),              # heat index C
    "visibility": ((50, 1.0), (200, 0.775), (400, 0.5), (1000, 0.2), (2000, 0.0)),  # m
}
CATEGORIES = ((75, "critical"), (50, "high"), (25, "medium"), (0, "low"))
RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}
CLIMATOLOGY_BOOST = 1.15
FORECAST_WEIGHT = 0.8
RECENT = timedelta(minutes=15)
ACCUMULATION = timedelta(hours=3)
ANTECEDENT_WEIGHT = 0.25
INPUT_KEYS = ("rain_mmph", "rain_accum_mm", "gust_kmph", "temperature_c", "humidity_pct", "visibility_m")


def interp(x, anchors):
    if x <= anchors[0][0]:
        return anchors[0][1]
    for (x0, y0), (x1, y1) in zip(anchors, anchors[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return anchors[-1][1]


def heat_index_c(temp_c, rh):
    """NOAA/NWS heat index (Rothfusz regression with its adjustments)."""
    f = temp_c * 9 / 5 + 32
    hi = 0.5 * (f + 61 + (f - 68) * 1.2 + rh * 0.094)
    if (hi + f) / 2 >= 80:
        hi = (-42.379 + 2.04901523 * f + 10.14333127 * rh - 0.22475541 * f * rh
              - 0.00683783 * f * f - 0.05481717 * rh * rh + 0.00122874 * f * f * rh
              + 0.00085282 * f * rh * rh - 0.00000199 * f * f * rh * rh)
        if rh < 13 and 80 <= f <= 112:
            hi -= ((13 - rh) / 4) * math.sqrt((17 - abs(f - 95)) / 17)
        elif rh > 85 and 80 <= f <= 87:
            hi += ((rh - 85) / 10) * ((87 - f) / 5)
    return (hi - 32) * 5 / 9


def category(score):
    return next(name for floor, name in CATEGORIES if score >= floor)


def _heat_input(inp):
    t, rh = inp.get("temperature_c"), inp.get("humidity_pct")
    if t is None:
        return None
    return t if rh is None else heat_index_c(t, rh)


def factors(inp):
    rain, accum = inp.get("rain_mmph"), inp.get("rain_accum_mm")
    gust, vis, heat = inp.get("gust_kmph"), inp.get("visibility_m"), _heat_input(inp)
    return {
        "rain": max(0.0 if rain is None else interp(rain, ANCHORS["rain_intensity"]),
                    0.0 if accum is None else interp(accum, ANCHORS["rain_accum"])),
        "wind": 0.0 if gust is None else interp(gust, ANCHORS["gust"]),
        "heat": 0.0 if heat is None else interp(heat, ANCHORS["heat"]),
        "fog": 0.0 if vis is None else interp(vis, ANCHORS["visibility"]),
    }


def _combine(f):
    return 1 - math.prod(1 - s for s in f.values())


def _dominant(f):
    hazard = max(f, key=f.get)
    return hazard if f[hazard] > 0 else None


def _beyond_normal(hazard, inp, clim):
    if not clim or hazard is None:
        return False
    if hazard == "rain":
        value, limit = inp.get("rain_mmph"), clim.get("rain_p95")
    elif hazard == "wind":
        value, limit = inp.get("gust_kmph"), clim.get("gust_p95")
    elif hazard == "heat":
        value, limit = _heat_input(inp), clim.get("heat_p95")
    else:
        value, limit = clim.get("vis_p5"), inp.get("visibility_m")   # lower visibility is worse
    return value is not None and limit is not None and value > limit


def _hazard_level(inp, clim):
    f = factors(inp)
    dominant = _dominant(f)
    unusual = _beyond_normal(dominant, inp, clim)
    h = _combine(f) * (CLIMATOLOGY_BOOST if unusual else 1.0)
    return f, dominant, unusual, round(100 * min(h, 1.0))


def assess(inputs, clim=None, forecast_inputs=None):
    f, hazard, unusual, now_score = _hazard_level(inputs, clim)
    score, developing, fc_score, fc_category = now_score, False, None, None
    if forecast_inputs:
        _, fc_hazard, _, fc_score = _hazard_level(forecast_inputs, clim)
        fc_category = category(fc_score)
        developing = RANK[fc_category] > RANK[category(now_score)]
        score = max(now_score, round(FORECAST_WEIGHT * fc_score))
        if hazard is None and developing:
            hazard = fc_hazard
    return {
        "score": score,
        "category": category(score),
        "hazard": hazard,
        "factors": {k: round(v, 3) for k, v in f.items()},
        "inputs": {k: inputs.get(k) for k in INPUT_KEYS},
        "unusual": unusual,
        "developing": developing,
        "forecast_score": fc_score,
        "forecast_category": fc_category,
    }


def summarize(windows, observed_at, rain_24h=None):
    """Feature windows (observed_from/observed_to as datetimes) -> risk inputs.

    'Recent' means windows ending in the last 15 observed minutes, or at least
    the latest one: at 120x replay a single 30 s window spans an observed hour.
    """
    if not windows:
        return {k: None for k in INPUT_KEYS}
    ws = sorted(windows, key=lambda w: w["observed_to"])
    latest = ws[-1]
    recent = [w for w in ws if w["observed_to"] >= observed_at - RECENT] or [latest]

    def values(key, rows):
        return [w[key] for w in rows if w.get(key) is not None]

    rain = values("rain_avg", recent)
    gust = values("gust_max", recent)
    vis = values("visibility_min", recent)

    start = observed_at - ACCUMULATION
    accum, prev = 0.0, None
    for w in ws:
        to = w["observed_to"]
        if prev is not None and w.get("rain_avg") is not None:
            seg_start = max(prev, start)
            if to > seg_start:
                accum += w["rain_avg"] * min(1.0, (to - seg_start).total_seconds() / 3600)
        prev = to
    if rain_24h is None:
        antecedent = values("rain_24h", ws)
        rain_24h = antecedent[-1] if antecedent else None
    if rain_24h is not None:
        accum += ANTECEDENT_WEIGHT * rain_24h

    return {
        "rain_mmph": sum(rain) / len(rain) if rain else None,
        "rain_accum_mm": accum,
        "gust_kmph": max(gust) if gust else None,
        "temperature_c": latest.get("temp_avg"),
        "humidity_pct": latest.get("humidity_avg"),
        "visibility_m": min(vis) if vis else None,
    }


def load_climatology(path=None):
    """city_id -> month ("1".."12") -> percentiles, from ml/climatology.py.
    Missing file means no climatology: the boost simply never applies."""
    import json
    from pathlib import Path

    path = Path(path) if path else Path(__file__).with_name("climatology.json")
    return json.loads(path.read_text()) if path.exists() else {}
