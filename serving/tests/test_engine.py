from datetime import datetime, timedelta, timezone

import pytest

from serving.engine import Engine
from serving.tests.helpers import START, SimRun
from weatherops.network import NETWORK


@pytest.fixture(scope="module")
def storm():
    """One storm-chennai run shared by the storm assertions (it is the slow part)."""
    run = SimRun(Engine(NETWORK))
    calm = run.advance(10)      # observed +0:00 .. +0:50, before the cell forms
    rest = run.advance(36)      # through the cell's peak over Sriperumbudur
    return calm, rest


def test_before_the_storm_everything_is_low(storm):
    calm, _ = storm
    last = calm[-1]
    assert last["locations"]
    assert all(loc["assessment"] is None or loc["assessment"]["category"] == "low"
               for loc in last["locations"].values())
    assert last["incidents"] == []


def test_storm_opens_an_incident_for_chennai_with_actions(storm):
    _, rest = storm
    opened = [e for r in rest for e in r["events"] if e["type"] == "opened"]
    che = [e["incident"] for e in opened if e["incident"]["region"] == "CHE"]
    assert che, [e["incident"]["region"] for e in opened]
    peak = max(rest, key=lambda r: r["kpis"]["deliveries_at_risk"])
    assert peak["kpis"]["routes_affected"]["lastmile"] > 0
    assert peak["kpis"]["deliveries_at_risk"] > 0
    assert peak["kpis"]["active_incidents"] >= 1
    inc = next(i for i in peak["incidents"] if i["region"] == "CHE")
    assert inc["actions"] and inc["actions"][0]["priority"] in {"P1", "P2"}


def test_storm_sensors_are_weather_not_faults(storm):
    _, rest = storm
    verdicts = {r["locations"][s.id]["verdict"]["status"]
                for r in rest for s in NETWORK.sensors_by_hub["CHE"] if s.id in r["locations"]}
    assert "weather_event" in verdicts or "ok" in verdicts
    assert "sensor_suspect" not in verdicts


def test_mode_reports_scenario_and_weather_clock(storm):
    _, rest = storm
    mode = rest[-1]["mode"]
    assert mode["source"] == "sim" and mode["scenario"] == "storm-chennai"
    assert mode["observed_at"] > "2026-11-20T08:00"
    assert 20 <= mode["speed"] <= 40


def test_isolated_spike_is_reported_and_ignored():
    run = SimRun(Engine(NETWORK))
    run.advance(12)
    run.faults.force("BLR-S1", "spike", run.now)
    results = run.advance(6)
    flagged = [r for r in results if any(s["station_id"] == "BLR-S1" for s in r["dq"]["suspect"])]
    assert flagged
    assert all(r["locations"]["BLR-S1"]["assessment"] is None for r in flagged)   # excluded from risk
    assert all(r["incidents"] == [] for r in results)


def test_hub_micro_event_is_weather():
    run = SimRun(Engine(NETWORK))
    run.advance(12)
    run.bank.start_micro_event("BLR", run.now, timedelta(minutes=10), rain=35, gust=25, cool=3)
    results = run.advance(12)
    statuses = [r["locations"][s.id]["verdict"]["status"]
                for r in results for s in NETWORK.sensors_by_hub["BLR"] if s.id in r["locations"]]
    assert "weather_event" in statuses
    assert "sensor_suspect" not in statuses


def test_detector_precision_and_recall_on_injected_faults():
    run = SimRun(Engine(NETWORK), fault_rate=6.0, seed=3)
    results = run.advance(60)
    flagged = {s["station_id"] for r in results for s in r["dq"]["suspect"]}
    detectable = {"spike", "stuck"}
    window = (START + timedelta(minutes=2), run.now - timedelta(minutes=1))
    truth = {sid for at, sid, kind in run.faults.log if kind in detectable | {"drift"}}
    spiked = {sid for at, sid, kind in run.faults.log if kind in detectable and window[0] <= at <= window[1]}
    precision = len(flagged & truth) / len(flagged)
    recall = len(flagged & spiked) / len(spiked)
    assert precision >= 0.8, (precision, sorted(flagged - truth))
    assert recall >= 0.7, (recall, sorted(spiked - flagged))


def window(station, start, seq_min, seq_max, rain=0.0, source="sim", scenario="storm-chennai",
           observed=None, rain_24h=None):
    end = start + timedelta(seconds=30)
    obs = observed or start
    iso = lambda d: d.strftime("%Y-%m-%dT%H:%M:%S.000Z")  # noqa: E731
    return {"station_id": station, "kind": NETWORK.stations[station].kind, "source": source,
            "scenario": scenario, "window_start": iso(start), "window_end": iso(end),
            "observed_from": iso(obs), "observed_to": iso(obs), "last_event_at": iso(end - timedelta(seconds=1)),
            "readings": seq_max - seq_min + 1, "seq_min": seq_min, "seq_max": seq_max, "delayed": 0,
            "max_delay_s": 0.5, "temp_min": 27.0, "temp_avg": 27.5, "temp_max": 28.0, "humidity_avg": 80.0,
            "rain_avg": rain, "rain_max": rain, "wind_avg": 10.0, "gust_max": 20.0, "visibility_min": 9000.0,
            "pressure_avg": 1007.0, "rain_24h": rain_24h}


def test_ingestor_restart_is_not_counted_as_missing_readings():
    e = Engine(NETWORK)
    e.ingest_window(window("CHE-S1", START, 498, 500))
    e.ingest_window(window("CHE-S1", START + timedelta(seconds=30), 1, 3))
    assert e.tick(START + timedelta(seconds=60))["dq"]["missing_readings"] == 0


def test_seq_gap_is_counted_as_missing():
    e = Engine(NETWORK)
    e.ingest_window(window("CHE-S1", START, 1, 3))
    e.ingest_window(window("CHE-S1", START + timedelta(seconds=30), 7, 9))
    assert e.tick(START + timedelta(seconds=60))["dq"]["missing_readings"] == 3


def test_hub_without_trusted_sensors_falls_back_to_the_city_reference():
    e = Engine(NETWORK)
    for k in range(3):
        e.ingest_window(window("REF-CHE", START + k * timedelta(seconds=30), k + 1, k + 1, rain=40))
    out = e.tick(START + timedelta(seconds=100))
    assert out["hubs"]["CHE"]["status"] in {"high", "critical"}
    assert out["hubs"]["BLR"]["status"] == "unknown"


def test_scenario_change_resets_state():
    e = Engine(NETWORK)
    for k in range(3):
        e.ingest_window(window("REF-CHE", START + k * timedelta(seconds=30), k + 1, k + 1, rain=40))
    for _ in range(3):
        e.tick(START + timedelta(seconds=100))
    e.ingest_window(window("REF-DEL", START + timedelta(seconds=120), 1, 1, scenario="fog-north"))
    out = e.tick(START + timedelta(seconds=160))
    assert "REF-CHE" not in out["locations"]
    assert out["mode"]["scenario"] == "fog-north"
    assert out["incidents"] == []


def test_quarantine_and_spark_counters_reach_dq():
    e = Engine(NETWORK)
    e.ingest_quarantine({"reason": "humidity_pct_out_of_range", "station_id": "CHE-S1"})
    e.ingest_quarantine({"reason": "humidity_pct_out_of_range", "station_id": "CHE-S2"})
    e.ingest_spark_counters(dropped_late=4, dropped_duplicates=7)
    dq = e.tick(START)["dq"]
    assert dq["quarantine"] == {"humidity_pct_out_of_range": 2}
    assert (dq["dropped_late"], dq["dropped_duplicates"]) == (4, 7)


def test_empty_engine_tick_is_well_formed():
    out = Engine(NETWORK).tick(datetime(2026, 10, 1, tzinfo=timezone.utc))
    assert out["locations"] == {} and out["incidents"] == [] and out["events"] == []
    assert out["kpis"]["active_incidents"] == 0
    assert out["mode"] is None
    assert out["health"]["latency_p95_s"] is None


def test_location_values_are_rounded_for_the_wire():
    e = Engine(NETWORK)
    w = window("REF-CHE", START, 1, 3, rain=12.345678)
    w["temp_avg"] = 27.123456
    e.ingest_window(w)
    loc = e.tick(START + timedelta(seconds=40))["locations"]["REF-CHE"]
    assert loc["values"]["rain_avg"] == 12.3 and loc["values"]["temp_avg"] == 27.1
    assert "inputs" not in loc["assessment"]      # engine-internal; not shipped to every client


class StubForecaster:
    card = {"version": "stub"}

    def __init__(self):
        self.calls = 0

    def predict(self, rows, current):
        self.calls += 1
        return [{"rain_mmph": 45.0, "gust_kmph": 30.0, "temperature_c": 26.0, "visibility_m": 4000.0,
                 "humidity_pct": c.get("humidity_pct"), "rain_accum_mm": None} for c in current]


def test_forecast_marks_developing_risk_and_raises_a_pre_alert():
    stub = StubForecaster()
    e = Engine(NETWORK, forecaster=stub)
    for k in range(3):
        e.ingest_window(window("REF-CHE", START + k * timedelta(seconds=30), k + 1, k + 1, rain=0.0,
                               observed=START + k * timedelta(hours=1)))
    out = e.tick(START + timedelta(seconds=100))
    loc = out["locations"]["REF-CHE"]
    assert loc["forecast"]["rain_mmph"] == 45.0
    assert loc["assessment"]["developing"] is True
    assert loc["assessment"]["category"] == "low"
    assert out["incidents"] == []
    [alert] = [a for a in out["prealerts"] if a["region"] == "CHE"]
    assert alert["forecast_category"] in {"high", "critical"}
    assert alert["actions"][0]["priority"] == "P3"
    calls = stub.calls
    e.tick(START + timedelta(seconds=110))          # no new reference data: no new prediction
    assert stub.calls == calls


def test_without_a_model_there_is_no_forecast():
    e = Engine(NETWORK)
    e.ingest_window(window("REF-CHE", START, 1, 1))
    out = e.tick(START + timedelta(seconds=40))
    assert out["locations"]["REF-CHE"]["forecast"] is None
    assert out["prealerts"] == []


def test_engine_runs_with_the_committed_models():
    from pathlib import Path

    from weatherops.forecast import Forecaster

    artifacts = Path(__file__).resolve().parents[2] / "ml" / "artifacts"
    fc = Forecaster.load(artifacts)
    if fc is None:
        pytest.skip("no trained models in ml/artifacts")
    run = SimRun(Engine(NETWORK, forecaster=fc))
    results = run.advance(8)
    loc = results[-1]["locations"]["REF-CHE"]
    assert loc["forecast"] is not None
    assert set(loc["forecast"]) >= {"rain_mmph", "gust_kmph", "temperature_c", "visibility_m"}
    assert loc["forecast"]["rain_mmph"] >= 0


def test_live_backfill_after_an_ingestor_restart_does_not_wipe_state():
    e = Engine(NETWORK)
    for k in range(3):
        e.ingest_window(window("REF-CHE", START + k * timedelta(seconds=30), k + 1, k + 1, rain=40,
                               source="live", scenario=None, observed=START + k * timedelta(seconds=30)))
    e.tick(START + timedelta(seconds=100))
    e.ingest_window(window("REF-CHE", START + timedelta(seconds=90), 4, 4, rain=40, source="live", scenario=None,
                           observed=START + timedelta(seconds=90)))
    e.tick(START + timedelta(seconds=110))
    assert e.incidents.active()
    # restarted ingestor replays the last three hours first
    e.ingest_window(window("REF-CHE", START + timedelta(seconds=120), 1, 1, source="live", scenario=None,
                           observed=START - timedelta(hours=3)))
    assert e.incidents.active(), "live backfill must not reset incidents"


def test_replay_loop_still_resets_when_the_clock_goes_back():
    e = Engine(NETWORK)
    e.ingest_window(window("REF-CHE", START, 1, 1, rain=40, source="replay", scenario="x",
                           observed=START + timedelta(hours=30)))
    e.ingest_window(window("REF-CHE", START + timedelta(seconds=30), 2, 2, source="replay", scenario="x",
                           observed=START))
    assert e.observed_at == START
