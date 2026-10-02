"""Rainfall: IMD-based severity classes, monthly climatology, Indian seasons,
the hourly replay record and a lead-time-degraded forecast built from it.

The simulator replays real hourly rain (truth); everything that looks ahead
(future-order risk, scenarios) sees only `forecast()`, which knows the truth
less well the further ahead it looks.
"""
import csv
import gzip
from datetime import timedelta

import numpy as np

CLASSES = ("none", "rain", "heavy", "extreme")
# IMD daily categories: light/moderate rain from 2.5 mm, heavy 64.5, very heavy 115.6+
# (very heavy and extremely heavy fold into "extreme").
DAILY_EDGES = (2.5, 64.5, 115.6)
# Hourly intensity: 1 mm/h is noticeable rain, 7.6 mm/h IMD "heavy", 20 mm/h cloudburst-like.
HOURLY_EDGES = (1.0, 7.6, 20.0)
SEASON_OF_MONTH = {1: "winter", 2: "winter", 3: "pre-monsoon", 4: "pre-monsoon", 5: "pre-monsoon",
                   6: "monsoon", 7: "monsoon", 8: "monsoon", 9: "monsoon",
                   10: "post-monsoon", 11: "post-monsoon", 12: "post-monsoon"}
SEASONS = ("pre-monsoon", "monsoon", "post-monsoon", "winter")
HOUR_FMT = "%Y-%m-%dT%H"


def _classify(value, edges):
    return CLASSES[sum(value >= e for e in edges)]


def daily_class(mm):
    return _classify(mm or 0.0, DAILY_EDGES)


def hourly_class(mmph):
    return _classify(mmph or 0.0, HOURLY_EDGES)


def season(month):
    return SEASON_OF_MONTH[month]


def climatology(rows):
    """rows of (city_id, 'YYYY-MM-DD', mm) -> {(city_id, month): stats}."""
    groups = {}
    for city_id, day, mm in rows:
        if mm in ("", None):
            continue
        groups.setdefault((city_id, int(day[5:7])), []).append(float(mm))
    out = {}
    for key, values in groups.items():
        a = np.asarray(values)
        out[key] = {
            "days": int(a.size),
            "mean_mm": round(float(a.mean()), 2),
            "median_mm": round(float(np.median(a)), 2),
            "p75_mm": round(float(np.percentile(a, 75)), 2),
            "p90_mm": round(float(np.percentile(a, 90)), 2),
            "p_rainy": round(float((a >= DAILY_EDGES[0]).mean()), 4),
            "p_heavy": round(float((a >= DAILY_EDGES[1]).mean()), 4),
            "monthly_total_mm": round(float(a.mean() * 30.4), 1),
        }
    return out


def read_daily(path):
    with gzip.open(path, "rt", newline="") as f:
        reader = csv.reader(f)
        next(reader)
        return [(c, d, float(mm) if mm else None) for c, d, mm in reader]


class HourlyRain:
    """Sparse hourly record: (city_id, 'YYYY-MM-DDTHH' UTC) -> mm/h; missing = dry."""

    def __init__(self, values):
        self.values = values

    @classmethod
    def load(cls, path):
        with gzip.open(path, "rt", newline="") as f:
            reader = csv.reader(f)
            next(reader)
            return cls({(c, t): float(v) for c, t, v in reader})

    def mmph(self, city_id, when):
        return self.values.get((city_id, when.strftime(HOUR_FMT)), 0.0)


def _accuracy(lead_h):
    # Hit rate of the forecast class: ~0.9 for the next hour, ~0.5 two days out.
    return max(0.5, 0.9 - 0.0085 * max(lead_h, 0))


def forecast(hourly, city_id, when, lead_h):
    """Probability of each class at `when`, forecast `lead_h` hours ahead.

    The true class keeps `accuracy`; the rest spreads to the neighbouring
    classes (a forecast misses by one category far more often than by two).
    Deterministic, so the same order scores the same on every tick.
    """
    mm = hourly.mmph(city_id, when)
    true = CLASSES.index(hourly_class(mm))
    acc = _accuracy(lead_h)
    probs = [0.0] * len(CLASSES)
    probs[true] = acc
    neighbours = [i for i in (true - 1, true + 1) if 0 <= i < len(CLASSES)]
    for i in neighbours:
        probs[i] += (1 - acc) / len(neighbours)
    return {
        "mmph": round(mm, 1),
        "probs": dict(zip(CLASSES, (round(p, 4) for p in probs))),
        "p_rain": round(1 - probs[0], 4),
    }


def hours(start, count):
    return [start + timedelta(hours=h) for h in range(count)]
