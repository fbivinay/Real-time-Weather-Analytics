import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ingestor import openmeteo
from weatherops.network import NETWORK

FIXTURES = Path(__file__).parent / "fixtures"
DELHI, CHENNAI = NETWORK.cities["DEL"], NETWORK.cities["CHE"]


def load(name):
    return json.loads((FIXTURES / name).read_text())


def test_parse_current_maps_every_city_and_converts_units():
    current, hourly = openmeteo.parse_current(load("current.json"), [DELHI, CHENNAI])
    assert set(current) == {"DEL", "CHE"}
    observed_at, cond = current["DEL"]
    assert observed_at == datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)
    # 0.6 mm in a 15-minute interval is 2.4 mm/h
    assert cond["rain_mmph"] == pytest.approx(2.4)
    assert cond["temperature_c"] == 33.6
    assert cond["humidity_pct"] == 36
    assert cond["wind_kmph"] == 12.8
    assert cond["gust_kmph"] == 31.0
    assert cond["visibility_m"] == 12180.0
    assert cond["pressure_hpa"] == 1011.2


def test_null_value_stays_none():
    current, _ = openmeteo.parse_current(load("current.json"), [DELHI, CHENNAI])
    assert current["CHE"][1]["visibility_m"] is None


def test_parse_current_returns_backfill_hours():
    _, hourly = openmeteo.parse_current(load("current.json"), [DELHI, CHENNAI])
    times = [t for t, _ in hourly["DEL"]]
    assert times == sorted(times)
    assert len(times) == 3
    assert hourly["DEL"][0][1]["temperature_c"] is not None


def test_single_city_payload_and_rain_24h():
    current, hourly = openmeteo.parse_current(load("current_single.json"), [CHENNAI])
    assert set(current) == {"CHE"}
    assert current["CHE"][1]["rain_24h_mm"] == pytest.approx(24.0)
    # precipitation-only hourly request: backfill samples carry rain only
    assert hourly["CHE"][-1][1]["rain_mmph"] == pytest.approx(1.0)


def test_error_payload_raises():
    with pytest.raises(openmeteo.OpenMeteoError, match="bad latitude"):
        openmeteo.parse_current({"error": True, "reason": "bad latitude"}, [DELHI])


def test_parse_hourly_in_time_order_with_rolling_rain_24h():
    series = openmeteo.parse_hourly(load("hourly.json"), [DELHI, CHENNAI])
    che = series["CHE"]
    assert len(che) == 24
    assert [t for t, _ in che] == sorted(t for t, _ in che)
    assert che[2][1]["visibility_m"] is None
    first_three = sum(c["rain_mmph"] for _, c in che[:3])
    assert che[2][1]["rain_24h_mm"] == pytest.approx(first_three)


def test_city_count_mismatch_raises():
    with pytest.raises(openmeteo.OpenMeteoError):
        openmeteo.parse_current(load("current.json"), [DELHI])


def test_request_url_lists_cities_in_order():
    url = openmeteo.current_url([DELHI, CHENNAI], hourly_vars=("precipitation",), past_hours=25)
    assert "latitude=28.6139%2C13.0827" in url
    assert "past_hours=25" in url
    assert "forecast_hours=0" in url
