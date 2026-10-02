"""Where reference weather comes from: Open-Meteo live, Open-Meteo history
replayed at speed, or a synthetic scenario. All three expose one interface,
so the ingestor loop never knows which it is driving.

Clocks: `tick(now)` returns `observed_at`, the weather time. Live follows
the wall clock after a 3-hour backfill played at 120x; replay and sim run a
compressed clock that loops. Pipeline time (`event_time`) is always `now`.
"""
import logging
from datetime import timedelta

from ingestor import events, openmeteo, scenarios
from ingestor.timeline import Timeline

log = logging.getLogger("ingestor.sources")


class LiveSource:
    source = "live"
    scenario = None
    sensor_smoothing_s = 300  # hides 15-minute reference steps on the sensors

    def __init__(self, network, fetch_current=openmeteo.fetch_current, poll_minutes=15,
                 backfill_hours=3, speed=120):
        self._cities = list(network.cities.values())
        self._fetch = fetch_current
        self._poll = timedelta(minutes=poll_minutes)
        self._backfill = timedelta(hours=backfill_hours)
        self.speed = speed
        self.timeline = Timeline()
        self._t0 = None
        self._next_poll = None
        self._failures = 0
        self._due = False

    def tick(self, now):
        if self._t0 is None:
            self._t0 = now
            self._fetch_into_timeline(now, hourly_vars=openmeteo.HOURLY_VARS,
                                      past_hours=int(self._backfill / timedelta(hours=1)) + 25)
        elif now >= self._next_poll:
            self._fetch_into_timeline(now)
        return self.observed_at(now)

    def observed_at(self, now):
        return min(now, now - self._backfill + (now - self._t0) * (self.speed - 1))

    def _fetch_into_timeline(self, now, hourly_vars=("precipitation",), past_hours=25):
        try:
            current, hourly = self._fetch(self._cities, hourly_vars=hourly_vars, past_hours=past_hours)
        except openmeteo.OpenMeteoError as exc:
            self._failures += 1
            wait = min(self._poll, timedelta(minutes=2 ** self._failures))
            log.warning("Open-Meteo poll failed (%s); keeping last values, retry in %s", exc, wait)
            self._next_poll = now + wait
            return
        if hourly_vars != ("precipitation",):
            for city_id, series in hourly.items():
                for t, cond in series:
                    self.timeline.add(city_id, t, cond)
        for city_id, (t, cond) in current.items():
            self.timeline.add(city_id, t, cond)
        self._failures = 0
        self._next_poll = now + self._poll
        self._due = True

    def reference_due(self, now):
        """True while backfilling, then once per successful poll."""
        if self.observed_at(now) < now:
            return True
        due, self._due = self._due, False
        return due

    def reference(self, city_id, observed_at):
        return self.timeline.at(city_id, observed_at)

    def reference_time(self, city_id, observed_at):
        span = self.timeline.span(city_id)
        return min(observed_at, span[1]) if span else observed_at

    def at_point(self, station, observed_at):
        return self.reference(station.city_id, observed_at)


class ReplaySource:
    source = "replay"
    sensor_smoothing_s = 0

    PREROLL = timedelta(hours=3)

    def __init__(self, network, event, fetch_hourly=openmeteo.fetch_hourly, speed=None):
        self.event = event
        self.scenario = event.id
        self.speed = speed or event.speed
        self._cities = list(network.cities.values())
        self._fetch = fetch_hourly
        self.timeline = None
        self._t0 = None
        self._retry_at = None

    @property
    def start(self):
        return self.event.start - self.PREROLL

    def _load(self, now):
        try:
            series = self._fetch(self._cities, self.start.date(), self.event.end.date())
        except openmeteo.OpenMeteoError as exc:
            log.warning("Replay history fetch failed (%s); retrying in 60 s", exc)
            self._retry_at = now + timedelta(seconds=60)
            return
        self.timeline = Timeline()
        for city_id, samples in series.items():
            for t, cond in samples:
                self.timeline.add(city_id, t, cond)

    def tick(self, now):
        if self.timeline is None and (self._retry_at is None or now >= self._retry_at):
            self._load(now)
        if self._t0 is None:
            self._t0 = now
        observed = self.start + (now - self._t0) * self.speed
        if observed > self.event.end:
            self._t0 = now
            observed = self.start
        return observed

    def reference_due(self, now):
        return True

    def reference(self, city_id, observed_at):
        return self.timeline.at(city_id, observed_at) if self.timeline else None

    def reference_time(self, city_id, observed_at):
        return observed_at

    def at_point(self, station, observed_at):
        return self.reference(station.city_id, observed_at)


class SimSource:
    source = "sim"
    sensor_smoothing_s = 0

    def __init__(self, network, scenario, speed=None):
        self._network = network
        self.sc = scenario
        self.scenario = scenario.id
        self.speed = speed or scenario.speed
        self._t0 = None

    def tick(self, now):
        if self._t0 is None:
            self._t0 = now
        elapsed = ((now - self._t0) * self.speed) % self.sc.duration
        return self.sc.start + elapsed

    def reference_due(self, now):
        return True

    def reference(self, city_id, observed_at):
        city = self._network.cities[city_id]
        c = scenarios.conditions_at(self.sc, city.lat, city.lon, observed_at, city)
        c["rain_24h_mm"] = scenarios.rain_24h(self.sc, city.lat, city.lon, observed_at)
        return c

    def reference_time(self, city_id, observed_at):
        return observed_at

    def at_point(self, station, observed_at):
        c = scenarios.conditions_at(self.sc, station.lat, station.lon, observed_at,
                                    self._network.cities[station.city_id])
        c["rain_24h_mm"] = None
        return c


def make_source(mode, scenario, network, speed=None, poll_minutes=15, **fetchers):
    if mode == "live":
        kwargs = {"fetch_current": fetchers["fetch_current"]} if "fetch_current" in fetchers else {}
        return LiveSource(network, poll_minutes=poll_minutes, **kwargs)
    if mode == "replay":
        if scenario not in events.EVENTS:
            raise ValueError(f"unknown replay event {scenario!r}; choose from {sorted(events.EVENTS)}")
        kwargs = {"fetch_hourly": fetchers["fetch_hourly"]} if "fetch_hourly" in fetchers else {}
        return ReplaySource(network, events.EVENTS[scenario], speed=speed, **kwargs)
    if mode == "sim":
        if scenario not in scenarios.SCENARIOS:
            raise ValueError(f"unknown sim scenario {scenario!r}; choose from {sorted(scenarios.SCENARIOS)}")
        return SimSource(network, scenarios.SCENARIOS[scenario], speed=speed)
    raise ValueError(f"unknown mode {mode!r}; choose live, replay or sim")
