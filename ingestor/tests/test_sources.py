from datetime import datetime, timedelta, timezone

import pytest

from ingestor import scenarios, sources
from ingestor.events import Event
from ingestor.openmeteo import OpenMeteoError
from weatherops import geo, schema
from weatherops.network import NETWORK

NOW = datetime(2026, 10, 1, 9, 12, tzinfo=timezone.utc)
H = timedelta(hours=1)


def cond(temp, rain=0.0):
    return {"temperature_c": temp, "humidity_pct": 50, "rain_mmph": rain, "wind_kmph": 10.0,
            "gust_kmph": 20.0, "visibility_m": 10000.0, "pressure_hpa": 1008.0, "rain_24h_mm": 0.0}


class FakeCurrent:
    """Stands in for openmeteo.fetch_current: hourly history at the top of
    each of the last 27 hours, current at the latest quarter hour."""

    def __init__(self):
        self.calls = []
        self.fail = False

    def __call__(self, cities, hourly_vars=("precipitation",), past_hours=25, timeout=20):
        self.calls.append(hourly_vars)
        if self.fail:
            raise OpenMeteoError("HTTP 429: too many requests")
        top = NOW.replace(minute=0)
        quarter = NOW.replace(minute=NOW.minute - NOW.minute % 15)
        temp = 30.0 + len(self.calls)
        current = {c.id: (quarter, cond(temp)) for c in cities}
        hourly = {c.id: [(top - k * H, cond(20.0 + k)) for k in range(past_hours, 0, -1)] for c in cities}
        return current, hourly


def live():
    return sources.LiveSource(NETWORK, fetch_current=FakeCurrent(), poll_minutes=15, backfill_hours=3, speed=120)


def test_live_first_tick_backfills_from_three_hours_ago():
    src = live()
    assert src.tick(NOW) == NOW - 3 * H
    assert src._fetch.calls[0] != ("precipitation",)  # all seven variables at start


def test_live_clock_runs_at_120x_then_follows_real_time():
    src = live()
    src.tick(NOW)
    assert src.tick(NOW + timedelta(seconds=45)) == NOW - 3 * H + timedelta(minutes=90)
    later = NOW + timedelta(seconds=120)
    assert src.tick(later) == later


def test_live_reference_due_during_backfill_then_only_after_polls():
    src = live()
    src.tick(NOW)
    assert src.reference_due(NOW)
    t = NOW + timedelta(seconds=100)
    src.tick(t)
    assert src.reference_due(t)       # first check after backfill: startup data not yet emitted live
    assert not src.reference_due(t)
    t2 = NOW + timedelta(minutes=16)
    src.tick(t2)                      # 15-minute poll
    assert src.reference_due(t2)
    assert not src.reference_due(t2)
    assert src._fetch.calls[-1] == ("precipitation",)


def test_live_backfill_reads_interpolated_history():
    src = live()
    obs = src.tick(NOW)
    ref = src.reference("DEL", obs)
    # three hours back from 09:12 is 06:12, between the 06:00 and 07:00 samples
    assert 22 < ref["temperature_c"] < 24


def test_live_reference_time_is_the_sample_time_after_backfill():
    src = live()
    src.tick(NOW)
    t = NOW + timedelta(seconds=200)
    obs = src.tick(t)
    assert src.reference_time("DEL", obs) == NOW.replace(minute=0)


def test_live_poll_failure_keeps_last_values_and_backs_off():
    src = live()
    src.tick(NOW)
    src._fetch.fail = True
    t = NOW + timedelta(minutes=16)
    obs = src.tick(t)
    assert src.reference("DEL", obs)["temperature_c"] == 31.0
    calls = len(src._fetch.calls)
    src.tick(t + timedelta(seconds=30))
    assert len(src._fetch.calls) == calls        # no retry storm
    src.tick(t + timedelta(minutes=3))
    assert len(src._fetch.calls) == calls + 1    # retried after backoff


def test_live_startup_failure_yields_no_reference():
    src = sources.LiveSource(NETWORK, fetch_current=FakeCurrent(), poll_minutes=15)
    src._fetch.fail = True
    obs = src.tick(NOW)
    assert src.reference("DEL", obs) is None


EVENT = Event("test-event", "Test event",
              datetime(2023, 12, 3, 0, 0, tzinfo=timezone.utc),
              datetime(2023, 12, 4, 0, 0, tzinfo=timezone.utc), ("CHE",), 120)


def fake_hourly(cities, start, end, url=None, timeout=60):
    t = datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
    stop = datetime(end.year, end.month, end.day, tzinfo=timezone.utc) + 24 * H
    out = {c.id: [] for c in cities}
    while t < stop:
        for c in cities:
            out[c.id].append((t, cond(25.0, rain=float(t.hour))))
        t += H
    return out


def test_replay_starts_three_hours_before_the_event():
    src = sources.ReplaySource(NETWORK, EVENT, fetch_hourly=fake_hourly)
    assert src.tick(NOW) == EVENT.start - 3 * H
    assert src.scenario == "test-event"
    assert src.reference_due(NOW)


def test_replay_runs_at_speed_and_loops_after_the_end():
    src = sources.ReplaySource(NETWORK, EVENT, fetch_hourly=fake_hourly)
    src.tick(NOW)
    assert src.tick(NOW + timedelta(seconds=30)) == EVENT.start - 3 * H + timedelta(hours=1)
    span = (EVENT.end - EVENT.start + 3 * H) / 120
    assert src.tick(NOW + span + timedelta(seconds=1)) == EVENT.start - 3 * H


def test_replay_interpolates_history():
    src = sources.ReplaySource(NETWORK, EVENT, fetch_hourly=fake_hourly)
    src.tick(NOW)
    ref = src.reference("CHE", datetime(2023, 12, 3, 5, 30, tzinfo=timezone.utc))
    assert ref["rain_mmph"] == pytest.approx(5.5)


def test_sim_storm_peaks_under_the_cell_and_is_absent_far_away():
    sc = scenarios.SCENARIOS["storm-chennai"]
    src = sources.SimSource(NETWORK, sc)
    cell = sc.cells[0]
    peak = sc.start + cell.start_offset + cell.duration / 2
    lat, lon = scenarios.cell_position(cell, 0.5)
    here = scenarios.conditions_at(sc, lat, lon, peak, NETWORK.cities["CHE"])
    assert here["rain_mmph"] >= 0.9 * cell.peak_rain_mmph
    far = geo.destination(lat, lon, 0, 300)
    assert scenarios.conditions_at(sc, *far, peak, NETWORK.cities["CHE"])["rain_mmph"] == pytest.approx(0, abs=0.01)
    before = sc.start + cell.start_offset - timedelta(minutes=1)
    assert scenarios.conditions_at(sc, lat, lon, before, NETWORK.cities["CHE"])["rain_mmph"] == 0


def test_sim_fog_drops_visibility_at_its_centre():
    sc = scenarios.SCENARIOS["fog-north"]
    fog = sc.fogs[0]
    mid = sc.start + fog.start_offset + fog.duration / 2
    vis = scenarios.conditions_at(sc, *fog.center, mid, NETWORK.cities["DEL"])["visibility_m"]
    assert vis <= fog.min_visibility_m * 1.1


def test_sim_at_point_differs_between_sensors_under_a_storm():
    sc = scenarios.SCENARIOS["storm-chennai"]
    src = sources.SimSource(NETWORK, sc)
    cell = sc.cells[0]
    peak = sc.start + cell.start_offset + cell.duration / 2
    rains = {s.id: src.at_point(s, peak)["rain_mmph"] for s in NETWORK.sensors_by_hub["CHE"]}
    assert max(rains.values()) > 0
    assert len(set(round(r, 3) for r in rains.values())) > 1


def test_sim_clock_loops():
    sc = scenarios.SCENARIOS["storm-chennai"]
    src = sources.SimSource(NETWORK, sc)
    assert src.tick(NOW) == sc.start
    assert src.tick(NOW + sc.duration / sc.speed + timedelta(seconds=1)) < sc.start + timedelta(minutes=1)


@pytest.mark.parametrize("scenario_id", sorted(scenarios.SCENARIOS))
def test_every_scenario_produces_valid_readings(scenario_id):
    sc = scenarios.SCENARIOS[scenario_id]
    src = sources.SimSource(NETWORK, sc)
    t = sc.start
    while t <= sc.start + sc.duration:
        for station in NETWORK.references + NETWORK.sensors[:20]:
            c = src.at_point(station, t)
            reading = {"station_id": station.id, "event_time": schema.fmt_ts(NOW), **c}
            assert schema.validate(reading, now=NOW) is None, (scenario_id, station.id, c)
        t += timedelta(minutes=30)


def test_make_source_rejects_unknown_scenarios():
    with pytest.raises(ValueError, match="storm-chennai"):
        sources.make_source("sim", "nope", NETWORK)
    with pytest.raises(ValueError):
        sources.make_source("teleport", None, NETWORK)
