import math
import random
from datetime import datetime, timedelta, timezone

import pytest

from ingestor.sensors import SensorBank
from weatherops.network import NETWORK

T0 = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)
STEP = timedelta(seconds=10)


def base(temp=20.0, rain=0.0):
    return {"temperature_c": temp, "humidity_pct": 60.0, "rain_mmph": rain, "wind_kmph": 10.0,
            "gust_kmph": 18.0, "visibility_m": 10000.0, "pressure_hpa": 1008.0, "rain_24h_mm": None}


def bank(**kw):
    kw.setdefault("micro_rate_per_hour", 0)
    return SensorBank(NETWORK.sensors, random.Random(1), **kw)


def test_smoothing_follows_a_first_order_lag():
    b, sensor = bank(noise=False), NETWORK.sensors[0]
    b.read(sensor, base(20), T0, smoothing_s=300)
    t, value = T0, None
    for _ in range(30):
        t += STEP
        value = b.read(sensor, base(30), t, smoothing_s=300)["temperature_c"]
    assert value == pytest.approx(20 + 10 * (1 - math.exp(-1)), abs=0.05)


def test_no_smoothing_tracks_base_with_bounded_noise():
    b, sensor = bank(), NETWORK.sensors[0]
    temps = [b.read(sensor, base(25), T0 + k * STEP, smoothing_s=0)["temperature_c"] for k in range(1000)]
    mean = sum(temps) / len(temps)
    sd = math.sqrt(sum((x - mean) ** 2 for x in temps) / len(temps))
    assert abs(mean - 25) < 0.6          # static calibration bias is at most 0.5
    assert 0.2 < sd < 0.4


def test_rain_is_never_negative_and_gust_never_below_wind():
    b = bank()
    for k in range(500):
        for sensor in NETWORK.sensors[:5]:
            r = b.read(sensor, base(rain=0.3), T0 + k * STEP, smoothing_s=0)
            assert r["rain_mmph"] >= 0
            assert r["gust_kmph"] >= r["wind_kmph"]


def test_none_in_base_stays_none():
    b = bank()
    c = base()
    c["visibility_m"] = None
    assert b.read(NETWORK.sensors[0], c, T0, smoothing_s=0)["visibility_m"] is None


def test_micro_event_hits_every_sensor_of_one_hub_only():
    b = bank(micro_rate_per_hour=0, noise=False)
    b.start_micro_event("CHE", T0, duration=timedelta(minutes=20), rain=30, gust=20, cool=3)
    mid = T0 + timedelta(minutes=10)
    for sensor in NETWORK.sensors_by_hub["CHE"]:
        assert b.read(sensor, base(), mid, smoothing_s=0)["rain_mmph"] > 15
    for sensor in NETWORK.sensors_by_hub["BLR"]:
        assert b.read(sensor, base(), mid, smoothing_s=0)["rain_mmph"] == 0
    assert b.micro_events[0][0] == "CHE"


def test_micro_events_start_at_roughly_the_configured_rate():
    b = SensorBank(NETWORK.sensors, random.Random(3), micro_rate_per_hour=1 / 6)
    t = T0
    for _ in range(6 * 360):                       # six hours of 10 s steps
        for sensor in NETWORK.sensors:
            b.read(sensor, base(), t, smoothing_s=0)
        t += STEP
    # 25 hubs x 6 h x 1/6 per hour = 25 expected
    assert 10 <= len(b.micro_events) <= 40
