import random
from datetime import datetime, timedelta, timezone

from ingestor.faults import FaultInjector
from weatherops import schema

T0 = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)
STEP = timedelta(seconds=10)
MEASURED = ("temperature_c", "humidity_pct", "rain_mmph", "wind_kmph", "gust_kmph", "visibility_m", "pressure_hpa")


def reading(k=0):
    return {"station_id": "CHE-S1", "seq": k, "event_time": schema.fmt_ts(T0 + k * STEP),
            "temperature_c": 28.0 + 0.1 * k, "humidity_pct": 70.0, "rain_mmph": 1.0, "wind_kmph": 12.0,
            "gust_kmph": 20.0, "visibility_m": 9000.0, "pressure_hpa": 1007.0}


def injector():
    return FaultInjector(random.Random(5), rate_per_sensor_hour=0)


def test_clean_reading_passes_through_unchanged():
    fi = injector()
    assert fi.apply("CHE-S1", reading(), T0) == [(T0, reading())]


def test_duplicate_sends_the_same_message_twice():
    fi = injector()
    fi.force("CHE-S1", "duplicate", T0)
    out = fi.apply("CHE-S1", reading(), T0)
    assert len(out) == 2
    assert out[0][1] == out[1][1] == reading()
    assert T0 <= out[1][0] <= T0 + timedelta(seconds=5)


def test_delayed_send_goes_out_20_to_120_s_late():
    fi = injector()
    fi.force("CHE-S1", "delayed", T0)
    [(send_at, msg)] = fi.apply("CHE-S1", reading(), T0)
    assert T0 + timedelta(seconds=20) <= send_at <= T0 + timedelta(seconds=120)
    assert msg == reading()


def test_dropout_silences_the_sensor_then_it_resumes():
    fi = injector()
    fi.force("CHE-S1", "dropout", T0)
    silent = [fi.apply("CHE-S1", reading(k), T0 + k * STEP) for k in range(12)]   # 2 minutes
    assert all(out == [] for out in silent)
    later = T0 + timedelta(minutes=16)
    assert fi.apply("CHE-S1", reading(99), later) != []


def test_stuck_sensor_repeats_its_values():
    fi = injector()
    fi.force("CHE-S1", "stuck", T0)
    outs = [fi.apply("CHE-S1", reading(k), T0 + k * STEP)[0][1] for k in range(20)]
    for field in MEASURED:
        assert len({o[field] for o in outs}) == 1, field
    assert len({o["seq"] for o in outs}) == 20


def test_drift_adds_a_growing_temperature_offset():
    fi = injector()
    fi.force("CHE-S1", "drift", T0)
    first = fi.apply("CHE-S1", reading(0), T0)[0][1]["temperature_c"]
    after = fi.apply("CHE-S1", reading(0), T0 + timedelta(minutes=20))[0][1]["temperature_c"]
    assert first == reading(0)["temperature_c"]
    assert 2 <= after - first <= 4


def test_invalid_reading_fails_validation():
    for seed in range(8):
        fi = FaultInjector(random.Random(seed), rate_per_sensor_hour=0)
        fi.force("CHE-S1", "invalid", T0)
        [(_, msg)] = fi.apply("CHE-S1", reading(), T0)
        assert schema.validate(msg, now=T0) is not None


def test_spike_alters_exactly_one_reading():
    fi = injector()
    fi.force("CHE-S1", "spike", T0)
    spiked = fi.apply("CHE-S1", reading(0), T0)[0][1]
    assert spiked != reading(0)
    assert fi.apply("CHE-S1", reading(1), T0 + STEP)[0][1] == reading(1)


def test_ground_truth_is_logged():
    fi = injector()
    fi.force("CHE-S1", "stuck", T0)
    fi.apply("CHE-S1", reading(), T0)
    assert fi.log[0][1:] == ("CHE-S1", "stuck")


def test_random_episodes_follow_the_rate():
    fi = FaultInjector(random.Random(11), rate_per_sensor_hour=0.25, interval_s=10)
    for k in range(10_000):
        fi.apply("CHE-S1", reading(k), T0 + k * STEP)
    # 0.25 per sensor-hour over 27.8 hours, minus time spent inside episodes
    assert 2 <= len(fi.log) <= 12
