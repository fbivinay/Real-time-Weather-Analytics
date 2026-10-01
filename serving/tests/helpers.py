"""Test-only stand-ins for the parts of the pipeline the engine never sees:
the ingestor loop driven by a fake clock, and a pure-Python version of
Spark's 30-second feature windows (same fields as plans.features)."""
import random
from datetime import datetime, timedelta, timezone

from ingestor import main, scenarios, sources
from ingestor.faults import FaultInjector
from ingestor.sensors import SensorBank
from weatherops import schema
from weatherops.network import NETWORK

START = datetime(2026, 10, 1, 9, 0, 0, tzinfo=timezone.utc)
STEP = timedelta(seconds=10)


def _iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def windows_from_readings(readings, window_s=30, kafka_delay_s=1.0):
    groups, seen = {}, set()
    for r in readings:
        reason = schema.validate(r, now=schema.parse_ts(r["event_time"])) if r.get("event_time") else "unparseable"
        if reason:
            continue   # quarantined by Spark
        key = (r["station_id"], r["seq"])
        if key in seen:
            continue   # deduplicated
        seen.add(key)
        et = schema.parse_ts(r["event_time"])
        start = datetime.fromtimestamp(et.timestamp() - et.timestamp() % window_s, tz=timezone.utc)
        groups.setdefault((r["station_id"], start), []).append(r)

    out = []
    for (sid, start), rs in groups.items():
        def vals(f):
            return [x[f] for x in rs if x.get(f) is not None]

        def agg(fn, f):
            v = vals(f)
            return fn(v) if v else None

        def mean(v):
            return sum(v) / len(v)

        out.append({
            "station_id": sid, "kind": rs[0]["kind"], "source": rs[0]["source"], "scenario": rs[0]["scenario"],
            "window_start": _iso(start), "window_end": _iso(start + timedelta(seconds=window_s)),
            "observed_from": _iso(min(schema.parse_ts(x["observed_at"]) for x in rs)),
            "observed_to": _iso(max(schema.parse_ts(x["observed_at"]) for x in rs)),
            "last_event_at": _iso(max(schema.parse_ts(x["event_time"]) for x in rs)),
            "readings": len(rs), "seq_min": min(x["seq"] for x in rs), "seq_max": max(x["seq"] for x in rs),
            "delayed": 0, "max_delay_s": kafka_delay_s,
            "temp_min": agg(min, "temperature_c"), "temp_avg": agg(mean, "temperature_c"),
            "temp_max": agg(max, "temperature_c"), "humidity_avg": agg(mean, "humidity_pct"),
            "rain_avg": agg(mean, "rain_mmph"), "rain_max": agg(max, "rain_mmph"),
            "wind_avg": agg(mean, "wind_kmph"), "gust_max": agg(max, "gust_kmph"),
            "visibility_min": agg(min, "visibility_m"), "pressure_avg": agg(mean, "pressure_hpa"),
            "rain_24h": agg(max, "rain_24h_mm"),
        })
    return sorted(out, key=lambda w: (w["window_end"], w["station_id"]))


class SimRun:
    """Drives the real ingestor in sim mode on a fake clock and feeds the
    engine the windows Spark would have emitted by each tick."""

    def __init__(self, engine, scenario="storm-chennai", fault_rate=0.0, seed=0, micro_rate=0.0):
        self.engine = engine
        rand = random.Random(seed)
        self.readings = []
        self.bank = SensorBank(NETWORK.sensors, rand, micro_rate_per_hour=micro_rate)
        self.faults = FaultInjector(rand, rate_per_sensor_hour=fault_rate)
        self.ingestor = main.Ingestor(NETWORK, sources.SimSource(NETWORK, scenarios.SCENARIOS[scenario]),
                                      self.bank, self.faults, self.readings.append, interval_s=10)
        self.now = START
        self._fed = set()

    def advance(self, steps):
        results = []
        for _ in range(steps):
            self.ingestor.step(self.now)
            self.ingestor.flush(self.now)
            closed = self.now - timedelta(seconds=15)   # Spark's watermark
            for w in windows_from_readings(self.readings):
                key = (w["station_id"], w["window_start"])
                if key not in self._fed and schema.parse_ts(w["window_end"]) <= closed:
                    self._fed.add(key)
                    self.engine.ingest_window(w)
            results.append(self.engine.tick(self.now))
            self.now += STEP
        return results
