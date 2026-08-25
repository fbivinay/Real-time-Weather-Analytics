import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from transforms import check_alert


def base_reading(**overrides):
    reading = {
        "station_id": "ST001",
        "city": "Bengaluru",
        "timestamp": "2026-08-25T10:20:23Z",
        "temperature": 25.0,
        "humidity": 60,
        "rainfall": 2.0,
        "wind_speed": 15.0,
    }
    reading.update(overrides)
    return reading


def test_no_alert_within_normal_ranges():
    assert check_alert(base_reading()) is None


def test_heat_alert():
    result = check_alert(base_reading(temperature=44.3))
    assert result == {
        "record_type": "alert",
        "station_id": "ST001",
        "city": "Bengaluru",
        "timestamp": "2026-08-25T10:20:23Z",
        "alert_type": "heat",
        "field": "temperature",
        "value": 44.3,
        "threshold": 40,
    }


def test_heavy_rain_alert():
    result = check_alert(base_reading(rainfall=72.0))
    assert result["alert_type"] == "heavy_rain"
    assert result["field"] == "rainfall"
    assert result["value"] == 72.0
    assert result["threshold"] == 50


def test_high_wind_alert():
    result = check_alert(base_reading(wind_speed=68.1))
    assert result["alert_type"] == "high_wind"
    assert result["field"] == "wind_speed"
    assert result["value"] == 68.1
    assert result["threshold"] == 60


def test_boundary_exactly_at_threshold_does_not_alert():
    assert check_alert(base_reading(temperature=40.0)) is None
    assert check_alert(base_reading(rainfall=50.0)) is None
    assert check_alert(base_reading(wind_speed=60.0)) is None
