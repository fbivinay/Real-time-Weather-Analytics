import random
from datetime import datetime, timedelta, timezone

from ingestor import main, scenarios, sources
from ingestor.faults import FaultInjector
from ingestor.sensors import SensorBank
from ingestor.tests.test_sources import FakeCurrent
from weatherops import schema
from weatherops.network import NETWORK

NOW = datetime(2026, 10, 1, 9, 12, tzinfo=timezone.utc)
STEP = timedelta(seconds=10)


def build(source, fault_rate=0):
    sent = []
    rand = random.Random(0)
    ing = main.Ingestor(NETWORK, source, SensorBank(NETWORK.sensors, rand, micro_rate_per_hour=0),
                        FaultInjector(rand, rate_per_sensor_hour=fault_rate), sent.append, interval_s=10)
    return ing, sent


def sim():
    return sources.SimSource(NETWORK, scenarios.SCENARIOS["storm-chennai"])


def test_sim_step_emits_every_reference_and_sensor_all_valid():
    ing, sent = build(sim())
    ing.step(NOW)
    refs = [m for m in sent if m["kind"] == "reference"]
    sensors = [m for m in sent if m["kind"] == "sensor"]
    assert len(refs) == 40
    assert len(sensors) == len(NETWORK.sensors)
    for m in sent:
        assert schema.validate(m, now=NOW) is None, m
        assert m["source"] == "sim" and m["scenario"] == "storm-chennai"


def test_reading_carries_both_clocks():
    ing, sent = build(sim())
    ing.step(NOW)
    m = sent[0]
    assert m["event_time"] == schema.fmt_ts(NOW)
    assert m["observed_at"] == schema.fmt_ts(scenarios.SCENARIOS["storm-chennai"].start)


def test_seq_increments_per_station():
    ing, sent = build(sim())
    ing.step(NOW)
    ing.step(NOW + STEP)
    seqs = [m["seq"] for m in sent if m["station_id"] == "CHE-S1"]
    assert seqs == [1, 2]


def test_live_references_only_when_due():
    src = sources.LiveSource(NETWORK, fetch_current=FakeCurrent(), poll_minutes=15)
    ing, sent = build(src)
    t = NOW + timedelta(minutes=5)
    ing.step(NOW)                       # backfill begins
    ing.step(t)                         # caught up: the startup poll's data goes out once
    sent.clear()
    ing.step(t + STEP)                  # nothing new from Open-Meteo
    assert not any(m["kind"] == "reference" for m in sent)
    assert any(m["kind"] == "sensor" for m in sent)


def test_live_poll_failure_does_not_stop_sensors():
    src = sources.LiveSource(NETWORK, fetch_current=FakeCurrent(), poll_minutes=15)
    ing, sent = build(src)
    ing.step(NOW)
    src._fetch.fail = True
    sent.clear()
    ing.step(NOW + timedelta(minutes=16))
    assert sum(m["kind"] == "sensor" for m in sent) == len(NETWORK.sensors)


def test_delayed_fault_is_held_until_its_send_time():
    ing, sent = build(sim())
    ing.faults.force("CHE-S1", "delayed", NOW)
    ing.step(NOW)
    assert not [m for m in sent if m.get("station_id") == "CHE-S1"]
    ing.flush(NOW + timedelta(seconds=121))
    assert [m for m in sent if m.get("station_id") == "CHE-S1"]


def test_make_reading_rounds_and_drops_nan():
    station = NETWORK.stations["CHE-S1"]
    cond = {"temperature_c": 28.456, "humidity_pct": 70.6, "rain_mmph": float("nan"), "wind_kmph": 12.34,
            "gust_kmph": 20.06, "visibility_m": 9000.4, "pressure_hpa": 1007.25, "rain_24h_mm": None}
    m = main.make_reading(station, cond, "sim", "x", 3, NOW, NOW)
    assert m["temperature_c"] == 28.5
    assert m["humidity_pct"] == 71
    assert m["rain_mmph"] is None
    assert m["visibility_m"] == 9000
    assert m["lat"] == station.lat


def test_config_from_env():
    cfg = main.Config.from_env({"MODE": "sim", "SCENARIO": "fog-north", "REPLAY_SPEED": "60", "FAULT_RATE": "0.5"})
    assert (cfg.mode, cfg.scenario, cfg.speed, cfg.fault_rate) == ("sim", "fog-north", 60.0, 0.5)
    assert main.Config.from_env({}).mode == "live"
