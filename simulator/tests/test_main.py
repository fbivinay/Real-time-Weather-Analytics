import json
from datetime import datetime, timedelta, timezone

from serving.tests.fakes import FakeRedis
from simulator import main
from weatherops import events
from weatherops.rainfall import HourlyRain


class FakeProducer:
    def __init__(self):
        self.sent = []

    def send(self, topic, value, key=None):
        self.sent.append((topic, key, value))

    def flush(self):
        pass


def test_fresh_start_backfills_two_days_then_runs_at_speed():
    prod, r = FakeProducer(), FakeRedis()
    runner = main.Runner(prod, r, HourlyRain({}), speed=60)
    times = [events.parse_ts(v["sim_time"]) for _, _, v in prod.sent if events.validate(v) is None]
    assert max(times) - min(times) >= timedelta(hours=47)
    assert {t for t, _, _ in prod.sent} == {"shopflow-events"}
    before = len(prod.sent)
    runner.clock.real_start -= timedelta(seconds=5)        # five real seconds = five sim minutes
    runner.tick()
    assert len(prod.sent) > before
    saved = json.loads(r.get("sim:clock"))
    assert saved["offset_s"] >= 48 * 3600 + 5 * 60 - 1
    assert json.loads(r.get("health:simulator"))["events_total"] == len(prod.sent)


def test_restart_resumes_the_clock_without_backfill():
    r = FakeRedis()
    r.set("sim:clock", json.dumps({"offset_s": 3 * 86400}))
    prod = FakeProducer()
    runner = main.Runner(prod, r, HourlyRain({}), speed=60)
    assert prod.sent == []
    assert runner.clock.now(runner.clock.real_start) == datetime(2025, 7, 4, tzinfo=timezone.utc)
