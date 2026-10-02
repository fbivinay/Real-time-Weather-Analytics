from datetime import datetime, timedelta, timezone

from weatherops import events
from weatherops.replay import SimClock

NOW = datetime(2026, 10, 2, 12, tzinfo=timezone.utc)


def good(**kw):
    e = events.make("ORDER_CREATED", datetime(2025, 7, 3, tzinfo=timezone.utc), {"a": 1},
                    order_id="ORD-1", city_id="MUM", now=NOW)
    e.update(kw)
    return e


def test_valid_event():
    assert events.validate(good(), NOW) is None


def test_reject_reasons_in_order():
    assert events.validate(good(event_id=None), NOW) == "unparseable"
    assert events.validate(good(sim_time="yesterday"), NOW) == "unparseable"
    assert events.validate(good(type="ORDER_EXPLODED"), NOW) == "unknown_type"
    assert events.validate(good(order_id=None), NOW) == "missing_order_id"
    assert events.validate(good(type="WEATHER_EVENT", city_id=None), NOW) == "missing_city_id"
    late = events.fmt_ts(NOW + timedelta(minutes=10))
    assert events.validate(good(emitted_at=late), NOW) == "future_timestamp"


def test_clock_runs_at_speed_and_wraps():
    real0 = datetime(2026, 10, 2, tzinfo=timezone.utc)
    clock = SimClock(start="2025-07-01T00:00:00Z", days=2, speed=60, real_start=real0)
    assert clock.now(real0 + timedelta(minutes=1)) == datetime(2025, 7, 1, 1, tzinfo=timezone.utc)
    after = real0 + timedelta(minutes=49)          # 49 sim hours: wrapped into day 1
    assert clock.now(after) == datetime(2025, 7, 1, 1, tzinfo=timezone.utc)
    assert clock.lap(after) == 1
