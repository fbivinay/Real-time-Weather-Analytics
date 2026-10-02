from datetime import datetime, timedelta, timezone

from weatherops import schema

NOW = datetime(2026, 10, 1, 10, 15, 30, tzinfo=timezone.utc)


def reading(**overrides):
    r = {
        "station_id": "BLR-S1",
        "kind": "sensor",
        "source": "live",
        "scenario": None,
        "seq": 1,
        "event_time": "2026-10-01T10:15:30Z",
        "observed_at": "2026-10-01T10:15:30Z",
        "lat": 13.07,
        "lon": 77.79,
        "temperature_c": 27.1,
        "humidity_pct": 74,
        "rain_mmph": 3.2,
        "wind_kmph": 14.0,
        "gust_kmph": 26.0,
        "visibility_m": 8000,
        "pressure_hpa": 1008.2,
        "rain_24h_mm": None,
    }
    r.update(overrides)
    return r


def test_normal_reading_is_valid():
    assert schema.validate(reading(), now=NOW) is None


def test_missing_station_is_unparseable():
    r = reading()
    del r["station_id"]
    assert schema.validate(r, now=NOW) == "unparseable"


def test_null_event_time_is_unparseable():
    assert schema.validate(reading(event_time=None), now=NOW) == "unparseable"


def test_garbage_event_time_is_unparseable():
    assert schema.validate(reading(event_time="yesterday"), now=NOW) == "unparseable"


def test_humidity_above_100_is_out_of_range():
    assert schema.validate(reading(humidity_pct=140), now=NOW) == "humidity_pct_out_of_range"


def test_negative_rain_is_out_of_range():
    assert schema.validate(reading(rain_mmph=-1), now=NOW) == "rain_mmph_out_of_range"


def test_first_failing_field_in_measurement_order_wins():
    r = reading(temperature_c=-99, humidity_pct=140)
    assert schema.validate(r, now=NOW) == "temperature_c_out_of_range"


def test_null_measurement_is_a_missing_value_not_invalid():
    assert schema.validate(reading(visibility_m=None), now=NOW) is None


def test_range_bounds_are_inclusive():
    assert schema.validate(reading(temperature_c=60), now=NOW) is None
    assert schema.validate(reading(humidity_pct=0), now=NOW) is None


def test_event_time_far_in_future_is_rejected():
    future = schema.fmt_ts(NOW + timedelta(minutes=5))
    assert schema.validate(reading(event_time=future), now=NOW) == "future_timestamp"


def test_event_time_within_tolerance_is_accepted():
    soon = schema.fmt_ts(NOW + timedelta(seconds=90))
    assert schema.validate(reading(event_time=soon), now=NOW) is None


def test_timestamp_round_trip():
    s = "2026-10-01T10:15:30Z"
    dt = schema.parse_ts(s)
    assert dt.tzinfo is not None
    assert dt == NOW
    assert schema.fmt_ts(dt) == s


def test_measurements_and_ranges_agree():
    assert set(schema.MEASUREMENTS) == set(schema.RANGES)


def test_parse_ts_accepts_spark_millisecond_format():
    # Spark's to_json writes timestamps as 2026-10-01T10:15:30.000Z
    assert schema.parse_ts("2026-10-01T10:15:30.000Z") == NOW
    assert schema.parse_ts("2026-10-01T10:15:30+00:00") == NOW
