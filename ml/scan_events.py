"""Find replay-worthy events in the downloaded history instead of trusting
memory: score every city-hour with the production risk function, then list
the strongest multi-city episodes per hazard.

    python -m ml.scan_events            # top episodes per hazard
"""
import sys

import numpy as np
import pandas as pd

from ml.climatology import load_history
from weatherops import risk

HIGH = 50


def score(frame):
    df = frame.sort_values(["city_id", "time"]).copy()
    rain = df.groupby("city_id")["rain_mmph"]
    df["accum"] = (rain.transform(lambda s: s.rolling(3, min_periods=1).sum())
                   + 0.25 * rain.transform(lambda s: s.rolling(24, min_periods=1).sum()))
    rows = zip(df["rain_mmph"], df["accum"], df["gust_kmph"], df["temperature_c"], df["humidity_pct"],
               df["visibility_m"])
    scored = []
    for r, a, g, t, h, v in rows:
        x = risk.assess({"rain_mmph": r, "rain_accum_mm": a, "gust_kmph": g, "temperature_c": t,
                         "humidity_pct": h, "visibility_m": v})
        scored.append((x["score"], x["hazard"]))   # tuples, not 1.4M dicts
    df["score"] = [s for s, _ in scored]
    df["hazard"] = [h for _, h in scored]
    return df


def episodes(df, hazard, top=6, gap_days=5):
    hot = df[(df["hazard"] == hazard) & (df["score"] >= HIGH)]
    if hot.empty:
        return []
    daily = hot.groupby(hot["time"].dt.floor("D")).agg(
        peak=("score", "max"), cities=("city_id", "nunique"), city_hours=("score", "size"),
        city_list=("city_id", lambda c: ",".join(sorted(set(c)))))
    daily["weight"] = daily["peak"] * np.log1p(daily["city_hours"])
    picked = []
    for day, row in daily.sort_values("weight", ascending=False).iterrows():
        if all(abs((day - d).days) > gap_days for d, _ in picked):
            picked.append((day, row))
        if len(picked) == top:
            break
    return picked


def main(hazards=("rain", "wind", "fog", "heat")):
    df = score(load_history())
    for hazard in hazards:
        print(f"\n== {hazard}")
        for day, row in episodes(df, hazard):
            print(f"  {day:%Y-%m-%d}  peak {int(row.peak)}  cities {row.cities:2d}  city-hours {row.city_hours:4d}  "
                  f"[{row.city_list}]")


if __name__ == "__main__":
    main(tuple(sys.argv[1:]) or ("rain", "wind", "fog", "heat"))
