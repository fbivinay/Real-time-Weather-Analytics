"""Download daily rainfall 2015-2025 for the 40 network cities from the
Open-Meteo archive (ERA5, CC BY 4.0) into data/rain_daily.csv.gz.

Open-Meteo weights a request by locations x two-week periods (600 per minute
free). Forty cities over half a year weigh ~520, so one half-year per request
and a pause between them. Re-running resumes from data/rain_daily.part.csv.

    python -m seed.fetch_rain
"""
import csv
import gzip
import json
import time
import urllib.request
from pathlib import Path

from weatherops.network import CITIES

ROOT = Path(__file__).resolve().parent.parent / "data"
OUT = ROOT / "rain_daily.csv.gz"
PART = ROOT / "rain_daily.part.csv"
URL = "https://archive-api.open-meteo.com/v1/archive"
PAUSE_S = 65
HALVES = [(f"{y}-01-01", f"{y}-06-30") for y in range(2015, 2026)] + \
         [(f"{y}-07-01", f"{y}-12-31") for y in range(2015, 2026)]


def fetch(start, end):
    query = (f"{URL}?latitude={','.join(str(c.lat) for c in CITIES)}"
             f"&longitude={','.join(str(c.lon) for c in CITIES)}"
             f"&start_date={start}&end_date={end}&daily=precipitation_sum&timezone=Asia%2FKolkata")
    with urllib.request.urlopen(query, timeout=300) as resp:
        body = json.load(resp)
    rows = []
    for city, loc in zip(CITIES, body):
        for day, mm in zip(loc["daily"]["time"], loc["daily"]["precipitation_sum"]):
            rows.append((city.id, day, "" if mm is None else round(mm, 1)))
    return rows


def main():
    ROOT.mkdir(exist_ok=True)
    done = set()
    if PART.exists():
        with PART.open() as f:
            done = {(r[0], r[1][:7]) for r in csv.reader(f)}
    halves = sorted(HALVES)
    for i, (start, end) in enumerate(halves):
        if (CITIES[0].id, start[:7]) in done:
            continue
        for attempt in range(5):
            try:
                rows = fetch(start, end)
                break
            except Exception as exc:  # rate limit or network: back off and retry
                print(f"{start}: {exc}; retry in {PAUSE_S * (attempt + 1)} s", flush=True)
                time.sleep(PAUSE_S * (attempt + 1))
        else:
            raise SystemExit(f"giving up on {start}")
        with PART.open("a", newline="") as f:
            csv.writer(f).writerows(rows)
        print(f"{start}..{end}: {len(rows)} rows ({i + 1}/{len(halves)})", flush=True)
        if i + 1 < len(halves):
            time.sleep(PAUSE_S)
    with PART.open() as f:
        rows = sorted(set(map(tuple, csv.reader(f))))
    with gzip.open(OUT, "wt", newline="") as f:
        w = csv.writer(f)
        w.writerow(("city_id", "date", "rain_mm"))
        w.writerows(rows)
    PART.unlink()
    print(f"{OUT}: {len(rows)} rows")


if __name__ == "__main__":
    main()
