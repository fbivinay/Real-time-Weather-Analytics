"""Simulator service: sim clock -> Simulator -> Kafka shopflow-events.

The clock position survives restarts (Redis key sim:clock). A fresh start,
and every lap of the replay window, first backfills BACKFILL_H simulated
hours as fast as Kafka takes them, so scheduled orders for the next two days
exist before the dashboard is opened.
"""
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from simulator.sim import Simulator
from weatherops import events
from weatherops.rainfall import HourlyRain
from weatherops.replay import REPLAY_DAYS, REPLAY_START, SPEED, SimClock

log = logging.getLogger("simulator")
TOPIC = "shopflow-events"
BACKFILL_H = 48
STEP = timedelta(minutes=1)
DATA = Path(os.environ.get("DATA_DIR", Path(__file__).resolve().parent.parent / "data"))


def producer():
    from kafka import KafkaProducer
    return KafkaProducer(
        bootstrap_servers=os.environ.get("KAFKA_BOOTSTRAP", "kafka.weather-pipeline.svc.cluster.local:9092"),
        value_serializer=lambda v: json.dumps(v, separators=(",", ":")).encode(),
        key_serializer=lambda k: k.encode() if k else None,
        linger_ms=50, acks=1, retries=5,
    )


def redis_client():
    import redis
    return redis.Redis(host=os.environ.get("REDIS_HOST", "redis-master.weather-pipeline.svc.cluster.local"),
                       port=6379, password=os.environ.get("REDIS_PASSWORD"), decode_responses=True,
                       socket_timeout=5)


class Runner:
    def __init__(self, prod, r, hourly, speed=SPEED, rate_scale=1.0):
        self.prod, self.r, self.hourly = prod, r, hourly
        self.speed, self.rate_scale = speed, rate_scale
        self.sent = 0
        saved = json.loads(r.get("sim:clock") or "null")
        if saved:
            self.clock = SimClock(speed=speed, offset_s=saved["offset_s"])
            self.lap = self.clock.lap()
            self._new_sim(self.clock.now() - STEP, backfill=False)
        else:
            self._start_lap()

    def _new_sim(self, start, backfill):
        self.sim = Simulator(self.hourly, start, seed=int(start.timestamp()), rate_scale=self.rate_scale)
        self.last = start
        if backfill:
            t = start
            while t < start + timedelta(hours=BACKFILL_H):
                t += STEP
                self._send(self.sim.step(t))
            self.prod.flush()
            self.last = t

    def _start_lap(self):
        start = events.parse_ts(REPLAY_START)
        log.info("starting replay lap at %s (backfilling %d h)", start, BACKFILL_H)
        self._new_sim(start, backfill=True)
        self.clock = SimClock(speed=self.speed, offset_s=BACKFILL_H * 3600)
        self.lap = self.clock.lap()

    def _send(self, evs):
        for e in evs:
            self.prod.send(TOPIC, value=e, key=e.get("order_id") or e.get("city_id"))
        self.sent += len(evs)

    def tick(self):
        if self.clock.lap() != self.lap:        # the replay window wrapped
            self._start_lap()
        now = self.clock.now()
        while self.last + STEP <= now:
            self.last += STEP
            self._send(self.sim.step(self.last))
        self.r.set("sim:clock", json.dumps({"offset_s": self.clock.elapsed_s()}))
        self.r.set("health:simulator", json.dumps({
            "sim_time": events.fmt_ts(self.last), "events_total": self.sent,
            "at": events.fmt_ts(datetime.now(timezone.utc)), "speed": self.speed,
            "replay": {"start": REPLAY_START, "days": REPLAY_DAYS},
        }), ex=120)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    hourly = HourlyRain.load(DATA / "rain_hourly.csv.gz")
    runner = Runner(producer(), redis_client(), hourly, speed=float(os.environ.get("SIM_SPEED", SPEED)),
                    rate_scale=float(os.environ.get("RATE_SCALE", 1.0)))
    while True:
        started = time.monotonic()
        runner.tick()
        time.sleep(max(0.0, 1.0 - (time.monotonic() - started)))


if __name__ == "__main__":
    main()
