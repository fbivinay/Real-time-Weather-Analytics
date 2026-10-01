"""Download hourly history for the 40 reference cities (2022-2025) from the
Open-Meteo Historical Forecast API into ml/data/hourly_<year>.parquet.

Open-Meteo weights a request by locations x two-week periods; the free tier
allows 600 weighted calls a minute and 10,000 a day. Forty cities over a
quarter weigh ~520, so one quarter per request and a pause between them
keeps a full run (~4,200 calls) inside both limits.

    python -m ml.fetch            # all years, skips files already present
"""
import sys
import time
from datetime import date
from pathlib import Path

import pandas as pd

from ingestor import openmeteo
from weatherops.network import NETWORK

DATA = Path(__file__).with_name("data")
YEARS = (2022, 2023, 2024, 2025)
QUARTERS = ((1, 1, 3, 31), (4, 1, 6, 30), (7, 1, 9, 30), (10, 1, 12, 31))
PAUSE_S = 65
FIELDS = ("temperature_c", "humidity_pct", "rain_mmph", "wind_kmph", "gust_kmph", "visibility_m", "pressure_hpa")


def to_frame(series):
    rows = [
        {"city_id": city_id, "time": t, **{f: cond.get(f) for f in FIELDS}}
        for city_id, samples in series.items()
        for t, cond in samples
    ]
    return pd.DataFrame(rows)


def fetch_quarter(year, quarter, retries=4):
    m0, d0, m1, d1 = quarter
    cities = list(NETWORK.cities.values())
    for attempt in range(retries):
        try:
            return to_frame(openmeteo.fetch_hourly(cities, date(year, m0, d0), date(year, m1, d1), timeout=300))
        except openmeteo.OpenMeteoError as exc:
            wait = PAUSE_S * (attempt + 1)
            print(f"  {year} Q{QUARTERS.index(quarter) + 1}: {exc}; retrying in {wait} s", flush=True)
            time.sleep(wait)
    raise SystemExit(f"giving up on {year} {quarter}")


def main(years=YEARS):
    DATA.mkdir(exist_ok=True)
    first = True
    for year in years:
        out = DATA / f"hourly_{year}.parquet"
        if out.exists():
            print(f"{out} exists, skipping")
            continue
        parts = []
        for quarter in QUARTERS:
            if not first:
                time.sleep(PAUSE_S)
            first = False
            print(f"fetching {year} {quarter}", flush=True)
            parts.append(fetch_quarter(year, quarter))
        frame = pd.concat(parts, ignore_index=True).sort_values(["city_id", "time"])
        frame.to_parquet(out, index=False)
        nulls = {f: round(frame[f].isna().mean() * 100, 2) for f in FIELDS}
        print(f"{out}: {len(frame):,} rows; null % {nulls}", flush=True)


if __name__ == "__main__":
    main(tuple(int(y) for y in sys.argv[1:]) or YEARS)
