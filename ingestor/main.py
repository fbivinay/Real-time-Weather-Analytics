"""Ingestor: city reference weather + simulated hub sensors -> Kafka.

Every SENSOR_INTERVAL_S it advances the source clock, emits the 40 city
references when the source has new data, derives every hub sensor's
reading, runs each sensor reading through fault injection, and sends what
is due. Delayed messages wait in a heap and go out on a later flush.

    MODE=live|replay|sim  SCENARIO=<event or scenario id>  REPLAY_SPEED=120
"""
import heapq
import itertools
import json
import logging
import math
import os
import random
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ingestor.faults import FaultInjector
from ingestor.sensors import SensorBank
from ingestor.sources import make_source
from weatherops.network import NETWORK
from weatherops.schema import fmt_ts

log = logging.getLogger("ingestor")

DECIMALS = {"temperature_c": 1, "humidity_pct": 0, "rain_mmph": 1, "wind_kmph": 1, "gust_kmph": 1,
            "visibility_m": 0, "pressure_hpa": 1, "rain_24h_mm": 1}


@dataclass
class Config:
    mode: str = "live"
    scenario: str | None = None
    speed: float | None = None
    sensor_interval_s: float = 10
    poll_minutes: int = 15
    fault_rate: float = 0.25
    seed: int = 0
    kafka_bootstrap: str = "kafka.weather-pipeline.svc.cluster.local:9092"
    topic: str = "weather-data"

    @classmethod
    def from_env(cls, env=os.environ):
        def get(name, cast, default):
            value = env.get(name)
            return cast(value) if value not in (None, "") else default

        return cls(
            mode=get("MODE", str, cls.mode),
            scenario=get("SCENARIO", str, None),
            speed=get("REPLAY_SPEED", float, None),
            sensor_interval_s=get("SENSOR_INTERVAL_S", float, cls.sensor_interval_s),
            poll_minutes=get("POLL_MINUTES", int, cls.poll_minutes),
            fault_rate=get("FAULT_RATE", float, cls.fault_rate),
            seed=get("SEED", int, cls.seed),
            kafka_bootstrap=get("KAFKA_BOOTSTRAP", str, cls.kafka_bootstrap),
            topic=get("TOPIC", str, cls.topic),
        )


def _clean(value, ndigits):
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    return int(round(value)) if ndigits == 0 else round(value, ndigits)


def make_reading(station, cond, source, scenario, seq, event_time, observed_at):
    reading = {
        "station_id": station.id,
        "kind": station.kind,
        "source": source,
        "scenario": scenario,
        "seq": seq,
        "event_time": fmt_ts(event_time),
        "observed_at": fmt_ts(observed_at),
        "lat": station.lat,
        "lon": station.lon,
    }
    for field, ndigits in DECIMALS.items():
        reading[field] = _clean(cond.get(field), ndigits)
    return reading


class Ingestor:
    def __init__(self, network, source, bank, faults, send, interval_s=10):
        self.network = network
        self.source = source
        self.bank = bank
        self.faults = faults
        self._send = send
        self.interval_s = interval_s
        self._seq = defaultdict(int)
        self._pending = []
        self._order = itertools.count()

    def _queue(self, station, cond, now, observed_at, faults):
        self._seq[station.id] += 1
        reading = make_reading(station, cond, self.source.source, self.source.scenario,
                               self._seq[station.id], now, observed_at)
        outgoing = self.faults.apply(station.id, reading, now) if faults else [(now, reading)]
        for send_at, msg in outgoing:
            heapq.heappush(self._pending, (send_at, next(self._order), msg))

    def step(self, now):
        observed = self.source.tick(now)
        refs = 0
        if self.source.reference_due(now):
            for ref in self.network.references:
                cond = self.source.reference(ref.city_id, observed)
                if cond is not None:
                    self._queue(ref, cond, now, self.source.reference_time(ref.city_id, observed), faults=False)
                    refs += 1
        sensors = 0
        for sensor in self.network.sensors:
            base = self.source.at_point(sensor, observed)
            if base is not None:
                cond = self.bank.read(sensor, base, now, self.source.sensor_smoothing_s)
                self._queue(sensor, cond, now, observed, faults=True)
                sensors += 1
        sent = self.flush(now)
        log.info("observed=%s refs=%d sensors=%d sent=%d held=%d",
                 fmt_ts(observed), refs, sensors, sent, len(self._pending))

    def flush(self, now):
        sent = 0
        while self._pending and self._pending[0][0] <= now:
            self._send(heapq.heappop(self._pending)[2])
            sent += 1
        return sent

    def run(self, after_step=lambda: None, clock=lambda: datetime.now(timezone.utc), sleep=time.sleep):
        next_step = clock()
        while True:
            now = clock()
            if now >= next_step:
                try:
                    self.step(now)
                except Exception:
                    # One bad cycle must not stop the feed; the next one retries.
                    log.exception("ingest cycle failed")
                after_step()
                next_step = max(next_step + timedelta(seconds=self.interval_s), now)
            elif self.flush(now):
                after_step()
            sleep(1)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    from kafka import KafkaProducer

    cfg = Config.from_env()
    rand = random.Random(cfg.seed)
    source = make_source(cfg.mode, cfg.scenario, NETWORK, speed=cfg.speed, poll_minutes=cfg.poll_minutes)
    producer = KafkaProducer(
        bootstrap_servers=cfg.kafka_bootstrap,
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
        linger_ms=20,
    )
    ingestor = Ingestor(
        NETWORK, source, SensorBank(NETWORK.sensors, rand),
        FaultInjector(rand, rate_per_sensor_hour=cfg.fault_rate, interval_s=cfg.sensor_interval_s),
        send=lambda msg: producer.send(cfg.topic, value=msg),
        interval_s=cfg.sensor_interval_s,
    )
    log.info("mode=%s scenario=%s speed=%s -> %s/%s", cfg.mode, cfg.scenario, source.speed,
             cfg.kafka_bootstrap, cfg.topic)
    ingestor.run(after_step=producer.flush)


if __name__ == "__main__":
    main()
