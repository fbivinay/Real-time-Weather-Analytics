"""Sensor fault injection, so the data-quality and anomaly paths always have
something real to catch. Ground truth goes to `log` (and the ingestor's
logs), never into the message - the pipeline has to work it out.
"""
import copy
import logging
from datetime import timedelta

log = logging.getLogger("ingestor.faults")

WEIGHTS = {"spike": 0.20, "stuck": 0.15, "drift": 0.10, "dropout": 0.15,
           "invalid": 0.15, "duplicate": 0.15, "delayed": 0.10}
EPISODES = {"stuck": (5, 30), "drift": (30, 60), "dropout": (2, 15)}  # minutes
MEASURED = ("temperature_c", "humidity_pct", "rain_mmph", "wind_kmph", "gust_kmph", "visibility_m", "pressure_hpa")


class FaultInjector:
    def __init__(self, rand, rate_per_sensor_hour=0.25, interval_s=10):
        self._rand = rand
        self._p = rate_per_sensor_hour * interval_s / 3600
        self._episodes = {}
        self._pending = {}
        self.log = []

    def force(self, station_id, kind, now):
        self._pending[station_id] = kind

    def _start(self, station_id, kind, now, reading):
        self.log.append((now, station_id, kind))
        log.info("fault %s on %s", kind, station_id)
        if kind in EPISODES:
            lo, hi = EPISODES[kind]
            ep = {"kind": kind, "start": now, "until": now + timedelta(minutes=self._rand.uniform(lo, hi))}
            if kind == "stuck":
                ep["frozen"] = {f: reading.get(f) for f in MEASURED}
            if kind == "drift":
                ep["rate"] = self._rand.uniform(0.1, 0.2)
            self._episodes[station_id] = ep
            return None
        return kind

    def apply(self, station_id, reading, now):
        """-> [(send_at, message), ...]; empty while the sensor is silent."""
        shot = None
        ep = self._episodes.get(station_id)
        if ep and now >= ep["until"]:
            del self._episodes[station_id]
            ep = None
        if station_id in self._pending:
            shot = self._start(station_id, self._pending.pop(station_id), now, reading)
            ep = self._episodes.get(station_id)
        elif ep is None and self._p > 0 and self._rand.random() < self._p:
            kind = self._rand.choices(list(WEIGHTS), weights=list(WEIGHTS.values()))[0]
            shot = self._start(station_id, kind, now, reading)
            ep = self._episodes.get(station_id)

        msg = copy.deepcopy(reading)
        if ep:
            if ep["kind"] == "dropout":
                return []
            if ep["kind"] == "stuck":
                msg.update(ep["frozen"])
            if ep["kind"] == "drift" and msg.get("temperature_c") is not None:
                minutes = (now - ep["start"]).total_seconds() / 60
                msg["temperature_c"] = round(msg["temperature_c"] + ep["rate"] * minutes, 1)

        r = self._rand
        if shot == "spike":
            field = r.choice(("temperature_c", "rain_mmph", "gust_kmph", "visibility_m"))
            if msg.get(field) is not None:
                if field == "temperature_c":
                    msg[field] = round(msg[field] + r.choice((-1, 1)) * r.uniform(15, 25), 1)
                elif field == "rain_mmph":
                    msg[field] = round(msg[field] + r.uniform(80, 200), 1)
                elif field == "gust_kmph":
                    msg[field] = round(msg[field] + r.uniform(60, 120), 1)
                else:
                    msg[field] = round(msg[field] * 0.05)
        elif shot == "invalid":
            variant = r.randrange(4)
            if variant == 0:
                msg["humidity_pct"] = 140
            elif variant == 1:
                msg["temperature_c"] = -99.0
            elif variant == 2:
                msg["rain_mmph"] = -5.0
            else:
                msg.pop("station_id", None)
        elif shot == "duplicate":
            return [(now, msg), (now + timedelta(seconds=r.uniform(0, 5)), copy.deepcopy(msg))]
        elif shot == "delayed":
            return [(now + timedelta(seconds=r.uniform(20, 120)), msg)]
        return [(now, msg)]
