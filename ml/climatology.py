"""What is normal for each city in each month - the "historical conditions"
input to the risk score. 20 mm/h is routine for Mumbai in July and rare for
Delhi in December; the score is boosted only when conditions exceed the
city's own 95th percentile (5th for visibility).

    python -m ml.climatology      # ml/data/*.parquet -> weatherops/climatology.json
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

from weatherops.risk import heat_index_c

DATA = Path(__file__).with_name("data")
OUT = Path(__file__).resolve().parent.parent / "weatherops" / "climatology.json"
RAINY_MMPH = 0.1
MIN_RAINY_HOURS = 20


def _q(series, q):
    values = series.dropna()
    return round(float(values.quantile(q)), 1) if len(values) else None


def build(frame):
    df = frame.copy()
    df["month"] = df["time"].dt.month
    df["heat"] = np.vectorize(heat_index_c, otypes=[float])(df["temperature_c"], df["humidity_pct"])
    out = {}
    for (city, month), g in df.groupby(["city_id", "month"]):
        rainy = g["rain_mmph"][g["rain_mmph"] >= RAINY_MMPH]
        out.setdefault(city, {})[str(month)] = {
            "rain_p95": _q(rainy, 0.95) if len(rainy) >= MIN_RAINY_HOURS else None,
            "gust_p95": _q(g["gust_kmph"], 0.95),
            "heat_p95": _q(g["heat"], 0.95),
            "vis_p5": _q(g["visibility_m"], 0.05),
        }
    return out


def load_history():
    files = sorted(DATA.glob("hourly_*.parquet"))
    if not files:
        raise SystemExit("no history in ml/data - run python -m ml.fetch first")
    return pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)


if __name__ == "__main__":
    clim = build(load_history())
    OUT.write_text(json.dumps(clim, separators=(",", ":"), sort_keys=True))
    print(f"{OUT}: {len(clim)} cities")
