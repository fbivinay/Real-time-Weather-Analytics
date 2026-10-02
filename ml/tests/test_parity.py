"""Training builds features vectorised in pandas; the engine builds them one
city at a time from its window buffer. They must agree, or the model sees
different inputs in production than it was trained on (silent skew)."""
import math
import random

import numpy as np
import pandas as pd
import pytest

from ml import dataset
from weatherops import forecast
from weatherops.network import NETWORK


def synthetic(city_ids=("CHE", "DEL"), hours=240, seed=1):
    rng = np.random.default_rng(seed)
    frames = []
    for city in city_ids:
        t = pd.date_range("2024-07-01", periods=hours, freq="h", tz="UTC")
        vis = rng.uniform(500, 20000, hours)
        vis[rng.random(hours) < 0.05] = np.nan
        frames.append(pd.DataFrame({
            "city_id": city, "time": t,
            "temperature_c": 28 + 4 * np.sin(np.arange(hours) / 4) + rng.normal(0, 0.5, hours),
            "humidity_pct": rng.uniform(40, 95, hours),
            "rain_mmph": np.maximum(0, rng.normal(0, 4, hours)),
            "wind_kmph": rng.uniform(5, 30, hours),
            "gust_kmph": rng.uniform(10, 60, hours),
            "visibility_m": vis,
            "pressure_hpa": 1005 + rng.normal(0, 2, hours),
        }))
    return pd.concat(frames, ignore_index=True)


def test_vectorised_training_features_match_serving_features():
    frame = synthetic()
    X, y, meta = dataset.build(frame)
    assert list(X.columns) == list(forecast.FEATURES)
    rows = random.Random(0).sample(range(len(X)), 60)
    for i in rows:
        city, t = meta.iloc[i]["city_id"], meta.iloc[i]["time"].to_pydatetime()
        c = NETWORK.cities[city]
        samples = [(r.time.to_pydatetime(), {f: (None if pd.isna(getattr(r, f)) else float(getattr(r, f)))
                                            for f in forecast.BASE})
                   for r in frame[frame.city_id == city].itertuples()]
        served = forecast.features_at(samples, t, c.lat, c.lon)
        for name, a, b in zip(forecast.FEATURES, X.iloc[i].tolist(), served):
            if math.isnan(a) or math.isnan(b):
                assert math.isnan(a) and math.isnan(b), (name, a, b)
            else:
                assert a == pytest.approx(b, abs=1e-6), (name, city, t, a, b)


def test_targets_are_the_next_hour():
    frame = synthetic(city_ids=("CHE",), hours=10)
    X, y, meta = dataset.build(frame)
    assert y["rain_mmph"].iloc[0] == pytest.approx(frame["rain_mmph"].iloc[1])
    assert math.isnan(y["rain_mmph"].iloc[-1])           # no hour after the last one


def test_lags_interpolate_between_windows_when_time_is_compressed():
    t0 = pd.Timestamp("2024-07-01T00:00Z").to_pydatetime()
    samples = [(t0, {f: 10.0 for f in forecast.BASE}),
               (t0 + pd.Timedelta(hours=2).to_pytimedelta(), {f: 20.0 for f in forecast.BASE})]
    feats = dict(zip(forecast.FEATURES, forecast.features_at(samples, samples[-1][0], 13.0, 80.0)))
    assert feats["temperature_c_lag1h"] == pytest.approx(15.0)
    assert math.isnan(feats["temperature_c_lag3h"])            # before the first sample: unknown, as in training
    far = [(t0, {f: 10.0 for f in forecast.BASE})]
    late = t0 + pd.Timedelta(hours=5).to_pytimedelta()
    assert math.isnan(dict(zip(forecast.FEATURES, forecast.features_at(far, late, 13.0, 80.0)))["temperature_c_lag1h"])
