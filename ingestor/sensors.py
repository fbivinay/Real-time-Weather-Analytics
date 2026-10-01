"""Company hub sensors, simulated in every mode.

A sensor reads its city's reference weather through a first-order lag (so
live mode's 15-minute reference steps arrive as smooth curves), plus a
static calibration bias and measurement noise. Hub micro-events - a local
downpour the regional model cannot see - hit every sensor at one hub
together; that shared signature is genuine weather, not a fault.
"""
import math
from datetime import timedelta

NOISE_SD = {"temperature_c": 0.3, "humidity_pct": 2.0, "wind_kmph": 1.5, "gust_kmph": 2.5, "pressure_hpa": 0.3}
VISIBILITY_CAP_M = 30000


class SensorBank:
    def __init__(self, sensors, rand, micro_rate_per_hour=1 / 6, noise=True):
        self._rand = rand
        self._noise = noise
        self._micro_rate = micro_rate_per_hour
        self._bias = {
            s.id: {"temperature_c": rand.uniform(-0.5, 0.5), "pressure_hpa": rand.uniform(-0.5, 0.5)}
            for s in sensors
        }
        self._state = {}
        self._last_draw = {}
        self._active = {}
        self.micro_events = []  # ground truth: (hub_id, start, end)

    def start_micro_event(self, hub_id, start, duration, rain, gust, cool):
        self._active[hub_id] = {"start": start, "end": start + duration, "rain": rain,
                                "gust": gust, "cool": cool, "mult": {}}
        self.micro_events.append((hub_id, start, start + duration))

    def _maybe_start(self, hub_id, now):
        last = self._last_draw.get(hub_id)
        self._last_draw[hub_id] = now
        if last is None or now <= last or self._micro_rate <= 0:
            return
        active = self._active.get(hub_id)
        if active and now < active["end"]:
            return
        p = 1 - math.exp(-self._micro_rate * (now - last) / timedelta(hours=1))
        if self._rand.random() < p:
            r = self._rand
            self.start_micro_event(hub_id, now, timedelta(minutes=r.uniform(10, 25)),
                                   rain=r.uniform(10, 40), gust=r.uniform(15, 30), cool=r.uniform(2, 4))

    def _micro_effect(self, station, now):
        ev = self._active.get(station.hub_id)
        if not ev or not ev["start"] <= now < ev["end"]:
            return 0.0, ev
        f = (now - ev["start"]) / (ev["end"] - ev["start"])
        mult = ev["mult"].setdefault(station.id, self._rand.uniform(0.8, 1.2))
        return math.sin(math.pi * f) * mult, ev

    def read(self, station, base, now, smoothing_s):
        self._maybe_start(station.hub_id, now)

        state = self._state.get(station.id)
        if state is None or smoothing_s <= 0:
            smooth = dict(base)
        else:
            alpha = 1 - math.exp(-max(0.0, (now - state["t"]).total_seconds()) / smoothing_s)
            smooth = {k: (None if v is None else v if state["v"].get(k) is None
                          else state["v"][k] + (v - state["v"][k]) * alpha)
                      for k, v in base.items()}
        self._state[station.id] = {"t": now, "v": smooth}

        out = dict(smooth)
        k, ev = self._micro_effect(station, now)
        if k:
            if out.get("rain_mmph") is not None:
                out["rain_mmph"] += ev["rain"] * k
            if out.get("gust_kmph") is not None:
                out["gust_kmph"] += ev["gust"] * k
            if out.get("wind_kmph") is not None:
                out["wind_kmph"] += 0.5 * ev["gust"] * k
            if out.get("temperature_c") is not None:
                out["temperature_c"] -= ev["cool"] * k
            if out.get("humidity_pct") is not None:
                out["humidity_pct"] += 15 * k
            if out.get("visibility_m") is not None:
                out["visibility_m"] *= 1 - 0.5 * k

        if self._noise:
            r = self._rand
            for field, sd in NOISE_SD.items():
                if out.get(field) is not None:
                    out[field] += r.gauss(0, sd) + self._bias[station.id].get(field, 0.0)
            if out.get("rain_mmph") is not None:
                out["rain_mmph"] *= 1 + r.gauss(0, 0.1)
            if out.get("visibility_m") is not None:
                out["visibility_m"] *= 1 + r.gauss(0, 0.05)

        if out.get("rain_mmph") is not None:
            out["rain_mmph"] = max(0.0, out["rain_mmph"])
        if out.get("humidity_pct") is not None:
            out["humidity_pct"] = min(100.0, max(0.0, out["humidity_pct"]))
        if out.get("visibility_m") is not None:
            out["visibility_m"] = min(VISIBILITY_CAP_M, max(0.0, out["visibility_m"]))
        if out.get("wind_kmph") is not None:
            out["wind_kmph"] = max(0.0, out["wind_kmph"])
        if out.get("gust_kmph") is not None and out.get("wind_kmph") is not None:
            out["gust_kmph"] = max(out["gust_kmph"], out["wind_kmph"])
        return out
