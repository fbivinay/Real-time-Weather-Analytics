"""Vectorised version of weatherops.forecast.features_at over hourly history,
plus the +1 h targets. test_parity pins the two together."""
import numpy as np
import pandas as pd

from weatherops import forecast
from weatherops.network import NETWORK


def _city_frame(g):
    g = g.sort_values("time").set_index("time")
    full = pd.date_range(g.index.min(), g.index.max(), freq="h")
    return g.reindex(full).rename_axis("time").reset_index()


def build(frame):
    """frame: hourly rows (city_id, time, BASE fields) -> (X, y, meta)."""
    parts = []
    for city, g in frame.groupby("city_id"):
        g = _city_frame(g)
        g["city_id"] = city
        parts.append(g)
    df = pd.concat(parts, ignore_index=True)

    vis = np.log10(df["visibility_m"].clip(lower=1.0))
    values = {f: (vis if f == "visibility_m" else df[f].astype(float)) for f in forecast.BASE}

    X = pd.DataFrame(index=df.index)
    for f in forecast.BASE:
        X[f"{f}_now"] = values[f]
    lagged = {}
    for f in forecast.LAGGED:
        series = values[f].groupby(df["city_id"], sort=False)
        for k in forecast.LAGS:
            lagged[(f, k)] = series.shift(k)
            X[f"{f}_lag{k}h"] = lagged[(f, k)]
    for f in forecast.LAGGED:
        for k in (1, 3):
            X[f"{f}_d{k}h"] = values[f] - lagged[(f, k)]
    rain = df["rain_mmph"].astype(float).groupby(df["city_id"], sort=False)
    X["rain_3h_sum"] = rain.transform(lambda s: s.rolling(3, min_periods=1).sum())
    X["rain_24h_sum"] = rain.transform(lambda s: s.rolling(24, min_periods=1).sum())
    hour = (df["time"].dt.hour + df["time"].dt.minute / 60 + forecast.IST_OFFSET_H) % 24
    doy = df["time"].dt.dayofyear
    X["hour_sin"], X["hour_cos"] = np.sin(2 * np.pi * hour / 24), np.cos(2 * np.pi * hour / 24)
    X["doy_sin"], X["doy_cos"] = np.sin(2 * np.pi * doy / 365.25), np.cos(2 * np.pi * doy / 365.25)
    X["lat"] = df["city_id"].map(lambda c: NETWORK.cities[c].lat)
    X["lon"] = df["city_id"].map(lambda c: NETWORK.cities[c].lon)
    X = X[list(forecast.FEATURES)]

    y = pd.DataFrame({t: values[t].groupby(df["city_id"], sort=False).shift(-1) for t in forecast.TARGETS})
    meta = df[["city_id", "time"]].copy()
    return X, y, meta
