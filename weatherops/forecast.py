"""+60 minute forecast: the per-city feature builder the engine uses, and a
thin wrapper around the trained LightGBM boosters.

Features use only what is known at time t: current values, 1/2/3-hour lags,
1 h and 3 h changes (the 3-hour pressure tendency is a classic storm
signal), recent rain sums, time of day and year, and location. Lags are
interpolated between buffered samples - at 120x replay one window spans an
observed hour - and are unknown (NaN) outside the buffered span or across a
gap longer than three hours, exactly as training's hourly shifts are.
ml/dataset.py builds the same features vectorised; tests pin the two.
"""
import bisect
import gzip
import json
import math
from datetime import timedelta
from pathlib import Path

BASE = ("temperature_c", "humidity_pct", "rain_mmph", "wind_kmph", "gust_kmph", "visibility_m", "pressure_hpa")
LAGGED = ("rain_mmph", "gust_kmph", "temperature_c", "visibility_m", "pressure_hpa")
LAGS = (1, 2, 3)
TARGETS = ("rain_mmph", "gust_kmph", "temperature_c", "visibility_m")
FEATURES = (
    [f"{f}_now" for f in BASE]
    + [f"{f}_lag{k}h" for f in LAGGED for k in LAGS]
    + [f"{f}_d{k}h" for f in LAGGED for k in (1, 3)]
    + ["rain_3h_sum", "rain_24h_sum", "hour_sin", "hour_cos", "doy_sin", "doy_cos", "lat", "lon"]
)
MAX_GAP = timedelta(hours=3)
IST_OFFSET_H = 5.5
NAN = float("nan")


def transform(field, value):
    """Visibility spans 50 m to 25 km; the model sees it on a log scale."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return NAN
    return math.log10(max(value, 1.0)) if field == "visibility_m" else float(value)


def time_features(t):
    hour = (t.hour + t.minute / 60 + IST_OFFSET_H) % 24
    doy = t.timetuple().tm_yday
    return [math.sin(2 * math.pi * hour / 24), math.cos(2 * math.pi * hour / 24),
            math.sin(2 * math.pi * doy / 365.25), math.cos(2 * math.pi * doy / 365.25)]


def _value_at(times, samples, field, x):
    i = bisect.bisect_left(times, x)
    if i < len(times) and times[i] == x:
        return transform(field, samples[i][1].get(field))
    if i == 0 or i == len(times):
        return NAN
    (ta, a), (tb, b) = samples[i - 1], samples[i]
    if tb - ta > MAX_GAP:
        return NAN
    va, vb = transform(field, a.get(field)), transform(field, b.get(field))
    if math.isnan(va) or math.isnan(vb):
        return NAN
    return va + (vb - va) * ((x - ta) / (tb - ta))


def features_at(samples, t, lat, lon):
    """samples: [(observed_at, conditions)] sorted by time; t: forecast origin."""
    times = [s[0] for s in samples]
    h = timedelta(hours=1)
    now = [_value_at(times, samples, f, t) for f in BASE]
    lag = {(f, k): _value_at(times, samples, f, t - k * h) for f in LAGGED for k in LAGS}
    cur = dict(zip(BASE, now))
    deltas = [cur[f] - lag[(f, k)] for f in LAGGED for k in (1, 3)]

    def rain_sum(hours):
        values = [_value_at(times, samples, "rain_mmph", t - k * h) for k in range(hours)]
        values = [v for v in values if not math.isnan(v)]
        return sum(values) if values else NAN

    return (now + [lag[(f, k)] for f in LAGGED for k in LAGS] + deltas
            + [rain_sum(3), rain_sum(24)] + time_features(t) + [lat, lon])


class Forecaster:
    """Loads gzipped LightGBM text models named in model_card.json; targets
    that did not beat persistence on the test year fall back to persistence."""

    def __init__(self, boosters, card):
        self.boosters = boosters
        self.card = card

    @classmethod
    def load(cls, directory):
        directory = Path(directory)
        card_path = directory / "model_card.json"
        if not card_path.exists():
            return None
        import lightgbm  # only the engine container needs it

        card = json.loads(card_path.read_text())
        boosters = {}
        for target, info in card["targets"].items():
            if info.get("shipped"):
                text = gzip.decompress((directory / info["file"]).read_bytes()).decode()
                boosters[target] = lightgbm.Booster(model_str=text)
        return cls(boosters, card)

    def predict(self, rows, current):
        """rows: feature vectors; current: each row's present conditions.
        Returns risk inputs for one hour ahead, per row."""
        import numpy as np

        X = np.array(rows, dtype=float)
        preds = {t: b.predict(X) for t, b in self.boosters.items()}
        out = []
        for i, now in enumerate(current):
            ahead = {}
            for target in TARGETS:
                if target in preds:
                    value = float(preds[target][i])
                    if target == "visibility_m":
                        value = 10 ** value
                    if target in ("rain_mmph", "gust_kmph"):
                        value = max(0.0, value)
                else:
                    value = now.get(target)
                ahead[target] = value
            ahead["humidity_pct"] = now.get("humidity_pct")
            ahead["rain_accum_mm"] = None
            out.append(ahead)
        return out
