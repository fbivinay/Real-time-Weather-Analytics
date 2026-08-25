from unittest.mock import MagicMock
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import weather_generator as wg


def test_stations_list_has_five_entries_with_correct_ids():
    ids = [s["station_id"] for s in wg.STATIONS]
    assert ids == ["ST001", "ST002", "ST003", "ST004", "ST005"]


def test_generate_reading_normal_no_extreme():
    fake_rand = MagicMock()
    fake_rand.uniform.side_effect = [20.0, 5.0, 15.0]
    fake_rand.randint.return_value = 60
    fake_rand.random.return_value = 0.5  # >= 1/30, no extreme trigger

    station = {"station_id": "ST001", "city": "Bengaluru"}
    reading = wg.generate_reading(station, rand=fake_rand)

    assert reading["station_id"] == "ST001"
    assert reading["city"] == "Bengaluru"
    assert reading["temperature"] == 20.0
    assert reading["humidity"] == 60
    assert reading["rainfall"] == 5.0
    assert reading["wind_speed"] == 15.0
    assert "timestamp" in reading


def test_generate_reading_extreme_triggers_temperature():
    fake_rand = MagicMock()
    fake_rand.uniform.side_effect = [20.0, 5.0, 15.0, 42.0]
    fake_rand.randint.return_value = 60
    fake_rand.random.return_value = 0.0  # < 1/30, triggers extreme
    fake_rand.choice.return_value = "temperature"

    station = {"station_id": "ST001", "city": "Bengaluru"}
    reading = wg.generate_reading(station, rand=fake_rand)

    assert reading["temperature"] == 42.0
    assert reading["rainfall"] == 5.0
    assert reading["wind_speed"] == 15.0


def test_generate_reading_extreme_triggers_rainfall():
    fake_rand = MagicMock()
    fake_rand.uniform.side_effect = [20.0, 5.0, 15.0, 75.0]
    fake_rand.randint.return_value = 60
    fake_rand.random.return_value = 0.0
    fake_rand.choice.return_value = "rainfall"

    station = {"station_id": "ST002", "city": "Mysuru"}
    reading = wg.generate_reading(station, rand=fake_rand)

    assert reading["temperature"] == 20.0
    assert reading["rainfall"] == 75.0
    assert reading["wind_speed"] == 15.0


def test_generate_reading_schema_has_exact_keys():
    fake_rand = MagicMock()
    fake_rand.uniform.side_effect = [20.0, 5.0, 15.0]
    fake_rand.randint.return_value = 60
    fake_rand.random.return_value = 0.5

    station = {"station_id": "ST001", "city": "Bengaluru"}
    reading = wg.generate_reading(station, rand=fake_rand)

    assert set(reading.keys()) == {
        "station_id", "city", "timestamp",
        "temperature", "humidity", "rainfall", "wind_speed",
    }


def test_generate_all_readings_returns_five_in_station_order():
    readings = wg.generate_all_readings()
    assert len(readings) == 5
    assert [r["station_id"] for r in readings] == \
        ["ST001", "ST002", "ST003", "ST004", "ST005"]
