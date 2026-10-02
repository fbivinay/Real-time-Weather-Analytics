"""Batch-mode tests of the streaming plans. They need Spark, so they run in
the apache/spark image:

  MSYS_NO_PATHCONV=1 docker run --rm --user 0 -v "$PWD:/repo" -w /repo \
    -e PYTHONPATH=/opt/spark/python:/opt/spark/python/lib/py4j-0.10.9.7-src.zip:/repo \
    apache/spark:3.5.3-python3 bash -c "pip install -q pytest && python3 -m pytest -q spark_processor/tests"
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("pyspark")

from pyspark.sql import SparkSession  # noqa: E402

from spark_processor import plans  # noqa: E402
from weatherops import events  # noqa: E402

T = datetime(2025, 7, 3, 9, tzinfo=timezone.utc)
NOW = datetime(2026, 1, 15, 10, 15, 31, tzinfo=timezone.utc)


def ev(type_="ORDER_CREATED", order_id="ORD-1", city_id="MUM", **kw):
    e = events.make(type_, T, {"delay_min": 30} if type_ == "DELIVERY_COMPLETED" else {},
                    order_id=order_id, city_id=city_id, route_id="R-MUM-MUM", now=NOW)
    e.update(kw)
    return e


@pytest.fixture(scope="module")
def spark():
    s = (SparkSession.builder.master("local[1]").appName("plans-test")
         .config("spark.sql.session.timeZone", "UTC")
         .config("spark.sql.shuffle.partitions", "1").getOrCreate())
    yield s
    s.stop()


def kafka_df(spark, messages):
    rows = [(bytearray((m if isinstance(m, str) else json.dumps(m)).encode()),
             (NOW + timedelta(seconds=1)).replace(tzinfo=None)) for m in messages]
    return spark.createDataFrame(rows, "value binary, timestamp timestamp")


def checked(spark, messages):
    return plans.with_reject_reason(plans.parse(kafka_df(spark, messages)))


PARITY = [
    ev(),
    ev(event_id=None),
    ev(sim_time="not a time"),
    ev(type="ORDER_EXPLODED"),
    ev(type=None),
    ev(order_id=None),
    ev("WEATHER_EVENT", order_id=None, city_id=None),
    ev("WEATHER_EVENT", order_id=None),
    ev(emitted_at="2099-01-01T00:00:00Z"),
]


def test_reject_reasons_match_python_validation(spark):
    out = checked(spark, PARITY).collect()
    now = datetime.now(timezone.utc)
    assert [row.reject_reason for row in out] == [events.validate(m, now=now) for m in PARITY]


def test_garbage_is_unparseable(spark):
    assert checked(spark, ["not json"]).collect()[0].reject_reason == "unparseable"


def test_quarantine_keeps_reason_and_truncated_raw(spark):
    big = ev(type="ORDER_EXPLODED", payload="x" * 3000)
    q = plans.quarantine(checked(spark, [ev(), big])).collect()
    assert len(q) == 1 and q[0].reason == "unknown_type" and len(q[0].raw) == 1024


def test_clean_drops_duplicates_and_invalid(spark):
    a, b = ev(), ev(order_id="ORD-2")
    rows = plans.clean(checked(spark, [a, dict(a), b, ev(type="X")])).collect()
    assert sorted(r.event_id for r in rows) == sorted([a["event_id"], b["event_id"]])
    assert set(rows[0].asDict()) == set(plans.CLEAN_COLUMNS)


def test_metrics_count_per_city(spark):
    msgs = [ev(), ev(order_id="ORD-2"), ev("DELIVERY_COMPLETED", order_id="ORD-3"),
            ev("VEHICLE_DISPATCHED", order_id=None), ev(city_id="BLR")]
    rows = {r.city_id: r for r in plans.metrics(plans.clean(checked(spark, msgs))).collect()}
    mum = rows["MUM"]
    assert (mum.events, mum.created, mum.completed, mum.dispatched) == (4, 2, 1, 1)
    assert mum.avg_delay_min == pytest.approx(30)
    assert rows["BLR"].created == 1


def test_streaming_dedup_then_window_in_append_mode(spark, tmp_path):
    """Production path: within-watermark dedup chained into a windowed
    aggregation in append mode (Spark >= 3.4)."""
    import time

    from pyspark.sql import functions as F

    src = tmp_path / "in"
    src.mkdir()
    a = ev(emitted_at="2026-01-15T10:15:31Z")
    later = ev(order_id="ORD-9", emitted_at="2026-01-15T10:17:31Z")   # moves the watermark on
    (src / "batch.json").write_text("\n".join(json.dumps(m) for m in [a, dict(a), later]))
    stream = spark.readStream.text(str(src)).select(
        F.col("value").cast("binary").alias("value"), F.current_timestamp().alias("timestamp"))
    query = (plans.metrics(plans.clean(plans.with_reject_reason(plans.parse(stream))))
             .writeStream.format("memory").queryName("metrics_stream").outputMode("append")
             .trigger(processingTime="1 second").option("checkpointLocation", str(tmp_path / "ck")).start())
    try:
        deadline = time.time() + 90
        rows = []
        while time.time() < deadline and not rows:
            time.sleep(1)
            rows = spark.sql("select * from metrics_stream").collect()
    finally:
        query.stop()
    first = [w for w in rows if w.window_start == datetime(2026, 1, 15, 10, 15, 30)]
    assert len(first) == 1 and first[0].events == 1     # the duplicate was dropped
