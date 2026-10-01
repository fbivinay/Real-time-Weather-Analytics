"""Batch-mode tests of the streaming plans. They need Spark, so they run in
the apache/spark image (see spark_processor/README section in the plan):

  docker run --rm --user 0 -v "$PWD:/repo" -w /repo \
    -e PYTHONPATH=/opt/spark/python:/opt/spark/python/lib/py4j-0.10.9.7-src.zip:/repo \
    apache/spark:3.5.3-python3 bash -c "pip install -q pytest && python3 -m pytest -q spark_processor/tests/test_plans.py"
"""
import copy
import json
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("pyspark")

from pyspark.sql import SparkSession  # noqa: E402

from spark_processor import plans  # noqa: E402
from weatherops import schema  # noqa: E402

BASE = {
    "station_id": "CHE-S1", "kind": "sensor", "source": "sim", "scenario": "storm-chennai", "seq": 1,
    "event_time": "2026-01-15T10:15:31Z", "observed_at": "2026-01-15T10:15:31Z", "lat": 12.97, "lon": 79.94,
    "temperature_c": 27.0, "humidity_pct": 80, "rain_mmph": 1.0, "wind_kmph": 10.0, "gust_kmph": 20.0,
    "visibility_m": 9000, "pressure_hpa": 1007.0, "rain_24h_mm": None,
}


def r(**kw):
    out = copy.deepcopy(BASE)
    out.update(kw)
    return out


@pytest.fixture(scope="module")
def spark():
    s = (SparkSession.builder.master("local[1]").appName("plans-test")
         .config("spark.sql.session.timeZone", "UTC")
         .config("spark.sql.shuffle.partitions", "1").getOrCreate())
    yield s
    s.stop()


def kafka_df(spark, messages, delays=None):
    """Rows shaped like the Kafka source: value (bytes) + timestamp."""
    rows = []
    for i, m in enumerate(messages):
        value = m if isinstance(m, str) else json.dumps(m)
        et = schema.parse_ts(m["event_time"]) if isinstance(m, dict) and m.get("event_time") else \
            datetime(2026, 1, 15, 10, 15, 31, tzinfo=timezone.utc)
        kafka_ts = et + timedelta(seconds=(delays or {}).get(i, 1))
        rows.append((bytearray(value.encode()), kafka_ts.replace(tzinfo=None)))
    return spark.createDataFrame(rows, "value binary, timestamp timestamp")


PARITY = [
    r(),
    r(humidity_pct=140),
    r(temperature_c=-99, humidity_pct=140),
    r(rain_mmph=-5.0),
    r(gust_kmph=999.0),
    r(visibility_m=None),
    r(temperature_c=60),
    r(station_id=None),
    r(event_time="2099-01-01T00:00:00Z"),
]


def test_reject_reasons_match_python_validation(spark):
    out = plans.with_reject_reason(plans.parse(kafka_df(spark, PARITY))).collect()
    now = datetime.now(timezone.utc)
    assert [row.reject_reason for row in out] == [schema.validate(m, now=now) for m in PARITY]


def test_garbage_is_unparseable(spark):
    out = plans.with_reject_reason(plans.parse(kafka_df(spark, ["not json"]))).collect()
    assert out[0].reject_reason == "unparseable"


def test_quarantine_keeps_reason_and_truncated_raw(spark):
    big = r(humidity_pct=140, scenario="x" * 3000)
    q = plans.quarantine(plans.with_reject_reason(plans.parse(kafka_df(spark, [r(), big])))).collect()
    assert len(q) == 1
    assert q[0].reason == "humidity_pct_out_of_range"
    assert len(q[0].raw) == 1024


def valid(spark, messages, delays=None):
    df = plans.with_reject_reason(plans.parse(kafka_df(spark, messages, delays)))
    return df.filter("reject_reason is null")


def test_window_aggregates_one_station(spark):
    msgs = [r(seq=1, rain_mmph=1.0, temperature_c=26.0, event_time="2026-01-15T10:15:31Z"),
            r(seq=2, rain_mmph=2.0, temperature_c=27.0, event_time="2026-01-15T10:15:41Z"),
            r(seq=3, rain_mmph=9.0, temperature_c=28.0, event_time="2026-01-15T10:15:51Z")]
    [w] = plans.features(valid(spark, msgs)).collect()
    assert w.readings == 3
    assert (w.seq_min, w.seq_max) == (1, 3)
    assert w.rain_max == 9.0
    assert w.rain_avg == pytest.approx(4.0)
    assert w.temp_avg == pytest.approx(27.0)
    assert (w.temp_min, w.temp_max) == (26.0, 28.0)
    assert w.window_start == datetime(2026, 1, 15, 10, 15, 30)
    assert w.delayed == 0


def test_duplicates_collapse(spark):
    [w] = plans.features(valid(spark, [r(seq=7), r(seq=7)])).collect()
    assert w.readings == 1


def test_delayed_readings_are_counted(spark):
    msgs = [r(seq=1), r(seq=2, event_time="2026-01-15T10:15:41Z")]
    [w] = plans.features(valid(spark, msgs, delays={1: 30})).collect()
    assert w.delayed == 1
    assert w.max_delay_s == pytest.approx(30)


def test_feature_json_has_the_contract_fields(spark):
    row = plans.features(valid(spark, [r()])).toJSON().first()
    keys = set(json.loads(row))
    assert {"station_id", "kind", "source", "scenario", "window_start", "window_end", "observed_from",
            "observed_to", "readings", "seq_min", "seq_max", "delayed", "max_delay_s", "temp_min", "temp_avg",
            "temp_max", "humidity_avg", "rain_avg", "rain_max", "wind_avg", "gust_max", "visibility_min",
            "pressure_avg"} <= keys


def test_streaming_dedup_then_window_in_append_mode(spark, tmp_path):
    """The production path: within-watermark dedup chained into a windowed
    aggregation, append mode. Needs Spark >= 3.4 (multiple stateful ops)."""
    import time

    from pyspark.sql import functions as F

    src = tmp_path / "in"
    src.mkdir()
    msgs = [r(seq=1, rain_mmph=2.0), r(seq=1, rain_mmph=2.0),
            r(seq=2, rain_mmph=4.0, event_time="2026-01-15T10:15:41Z"),
            r(seq=3, event_time="2026-01-15T10:17:00Z")]  # moves the watermark past the first window
    (src / "batch.json").write_text("\n".join(json.dumps(m) for m in msgs))

    stream = spark.readStream.text(str(src)).select(
        F.col("value").cast("binary").alias("value"), F.current_timestamp().alias("timestamp"))
    valid_stream = plans.with_reject_reason(plans.parse(stream)).filter("reject_reason is null")
    query = (plans.features(valid_stream).writeStream.format("memory").queryName("features_stream")
             .outputMode("append").trigger(processingTime="1 second")
             .option("checkpointLocation", str(tmp_path / "ck")).start())
    try:
        deadline = time.time() + 90
        rows = []
        while time.time() < deadline and not rows:
            time.sleep(1)
            rows = spark.sql("select * from features_stream").collect()
    finally:
        query.stop()

    first = [w for w in rows if w.window_start == datetime(2026, 1, 15, 10, 15, 30)]
    assert len(first) == 1
    assert first[0].readings == 2          # the duplicate seq=1 was dropped
    assert first[0].rain_max == 4.0
