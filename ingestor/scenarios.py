"""Synthetic weather with known ground truth, for tests and demos.

A scenario is a calm diurnal baseline plus moving storm cells, fog banks
and heat domes. Hazards are evaluated at each station's own coordinates, so
a cell crossing a hub reaches its sensors one after another - the spatial
signature that lets the anomaly detector tell weather from a broken sensor.
"""
import math
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from weatherops import geo


@dataclass(frozen=True)
class StormCell:
    track: tuple            # ((lat, lon), ...) start -> end
    start_offset: timedelta
    duration: timedelta
    radius_km: float
    peak_rain_mmph: float
    peak_gust_kmph: float


@dataclass(frozen=True)
class FogBank:
    center: tuple
    radius_km: float
    start_offset: timedelta
    duration: timedelta
    min_visibility_m: float


@dataclass(frozen=True)
class HeatDome:
    center: tuple
    radius_km: float
    anomaly_c: float


@dataclass(frozen=True)
class Scenario:
    id: str
    name: str
    start: datetime
    speed: float
    duration: timedelta
    cells: tuple = field(default_factory=tuple)
    fogs: tuple = field(default_factory=tuple)
    heats: tuple = field(default_factory=tuple)


def _utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


H = timedelta(hours=1)

SCENARIOS = {s.id: s for s in [
    Scenario(
        "storm-chennai", "Severe thunderstorm crossing Chennai",
        _utc(2026, 11, 20, 6), 30, 6 * H,
        cells=(StormCell(((12.55, 80.65), (13.15, 79.55)), 1 * H, 4 * H, 35, 45, 85),),
    ),
    Scenario(
        "monsoon-mumbai", "Monsoon bands over Mumbai and the Pune corridor",
        _utc(2026, 7, 15, 6), 30, 6 * H,
        cells=(
            StormCell(((18.70, 72.55), (19.45, 73.25)), 0.5 * H, 3 * H, 40, 60, 55),
            StormCell(((18.40, 72.70), (18.75, 73.75)), 2.5 * H, 3 * H, 35, 40, 50),
        ),
    ),
    Scenario(
        "fog-north", "Dense fog over Delhi NCR and Lucknow",
        _utc(2026, 12, 20, 20), 30, 8 * H,
        fogs=(
            FogBank((28.45, 77.05), 80, 0.5 * H, 6 * H, 80),
            FogBank((26.85, 80.95), 60, 1 * H, 5 * H, 150),
        ),
    ),
    Scenario(
        "heatwave-north", "Heatwave over the north-western plains",
        _utc(2026, 5, 25, 4), 30, 10 * H,
        heats=(HeatDome((27.0, 76.8), 450, 15),),
    ),
]}


def _profile(f):
    """Grow over the first quarter, hold, decay over the last quarter."""
    if f <= 0 or f >= 1:
        return 0.0
    if f < 0.25:
        return f / 0.25
    if f > 0.75:
        return (1 - f) / 0.25
    return 1.0


def _falloff(d_km, radius_km):
    return math.exp(-((d_km / radius_km) ** 2))


def cell_position(cell, f):
    pts = cell.track
    legs = [geo.haversine_km(*a, *b) for a, b in zip(pts, pts[1:])]
    target = f * sum(legs)
    for (a, b), leg in zip(zip(pts, pts[1:]), legs):
        if target <= leg or leg == 0:
            g = 0 if leg == 0 else target / leg
            return (a[0] + (b[0] - a[0]) * g, a[1] + (b[1] - a[1]) * g)
        target -= leg
    return pts[-1]


def _seed_offset(city_id, salt):
    return (zlib.crc32(f"{city_id}:{salt}".encode()) % 1000) / 1000 - 0.5


def baseline(city, t):
    """Calm conditions with a diurnal cycle on local solar time."""
    hour = (t.hour + t.minute / 60 + city.lon / 15) % 24
    diurnal = math.cos(2 * math.pi * (hour - 15) / 24)   # +1 mid-afternoon
    # Calm means calm: afternoon highs stay near 33 C so the baseline never
    # reaches the heat-index anchors by itself.
    mean = 29 - 0.25 * max(0.0, city.lat - 12) + _seed_offset(city.id, "t")
    wind = 8 + 4 * max(0.0, diurnal) + 2 * _seed_offset(city.id, "w")
    return {
        "temperature_c": mean + 4 * diurnal,
        "humidity_pct": 62 - 18 * diurnal,
        "rain_mmph": 0.0,
        "wind_kmph": wind,
        "gust_kmph": wind * 1.6,
        "visibility_m": 12000.0,
        "pressure_hpa": 1008 + 1.5 * math.cos(2 * math.pi * (hour - 10) / 12),
        "rain_24h_mm": 0.0,
    }


def _cell_effect(cell, lat, lon, t, start):
    f = (t - start - cell.start_offset) / cell.duration
    intensity = _profile(f)
    if intensity == 0:
        return 0.0
    return intensity * _falloff(geo.haversine_km(lat, lon, *cell_position(cell, f)), cell.radius_km)


def conditions_at(scenario, lat, lon, t, base_city):
    c = baseline(base_city, t)

    for cell in scenario.cells:
        k = _cell_effect(cell, lat, lon, t, scenario.start)
        if k:
            c["rain_mmph"] += cell.peak_rain_mmph * k
            c["gust_kmph"] += cell.peak_gust_kmph * k
            c["wind_kmph"] += 0.5 * cell.peak_gust_kmph * k
            c["temperature_c"] -= 4 * k
            c["humidity_pct"] += 30 * k
            c["visibility_m"] *= 1 - 0.8 * k
            c["pressure_hpa"] -= 8 * k

    for fog in scenario.fogs:
        f = (t - scenario.start - fog.start_offset) / fog.duration
        k = _profile(f) * _falloff(geo.haversine_km(lat, lon, *fog.center), fog.radius_km)
        if k:
            c["visibility_m"] *= (fog.min_visibility_m / c["visibility_m"]) ** k
            c["humidity_pct"] += (98 - c["humidity_pct"]) * k
            c["wind_kmph"] *= 1 - 0.7 * k
            c["gust_kmph"] *= 1 - 0.7 * k

    for heat in scenario.heats:
        k = _falloff(geo.haversine_km(lat, lon, *heat.center), heat.radius_km)
        c["temperature_c"] += heat.anomaly_c * k
        c["humidity_pct"] -= 35 * k

    c["humidity_pct"] = min(100.0, max(3.0, c["humidity_pct"]))
    c["gust_kmph"] = max(c["gust_kmph"], c["wind_kmph"])
    return c


def rain_24h(scenario, lat, lon, t, step=timedelta(minutes=15)):
    """Rain fallen over the previous 24 h of scenario time, by sampling."""
    total, s = 0.0, max(scenario.start, t - 24 * H)
    while s < t:
        for cell in scenario.cells:
            total += cell.peak_rain_mmph * _cell_effect(cell, lat, lon, s, scenario.start) * step / H
        s += step
    return total
