"""DataFrame -> DataFrame steps of the streaming job, kept free of sources
and sinks so the same code runs as a stream in production and as a batch
job in tests."""
from pyspark.sql import functions as F
from pyspark.sql.types import (
    DoubleType, LongType, StringType, StructField, StructType, TimestampType,
)

from weatherops.schema import FUTURE_TOLERANCE_S, MEASUREMENTS, RANGES

READING_SCHEMA = StructType([
    StructField("station_id", StringType()),
    StructField("kind", StringType()),
    StructField("source", StringType()),
    StructField("scenario", StringType()),
    StructField("seq", LongType()),
    StructField("event_time", TimestampType()),
    StructField("observed_at", TimestampType()),
    StructField("lat", DoubleType()),
    StructField("lon", DoubleType()),
    *[StructField(m, DoubleType()) for m in MEASUREMENTS],
])


def parse(kafka_df):
    raw = F.col("value").cast("string")
    return (
        kafka_df.select(raw.alias("raw"), F.col("timestamp").alias("kafka_ts"),
                        F.from_json(raw, READING_SCHEMA).alias("r"))
        .select("raw", "kafka_ts", "r.*")
    )


def reject_reason():
    """Same rule order as weatherops.schema.validate(); null = valid."""
    reason = F.when(
        F.col("station_id").isNull() | (F.col("station_id") == "") | F.col("event_time").isNull(),
        F.lit("unparseable"),
    )
    for field in MEASUREMENTS:
        lo, hi = RANGES[field]
        reason = reason.when((F.col(field) < lo) | (F.col(field) > hi), F.lit(f"{field}_out_of_range"))
    future = F.current_timestamp() + F.expr(f"INTERVAL {FUTURE_TOLERANCE_S} SECONDS")
    return reason.when(F.col("event_time") > future, F.lit("future_timestamp"))


def with_reject_reason(df):
    return df.withColumn("reject_reason", reject_reason())


def quarantine(df):
    return df.filter(F.col("reject_reason").isNotNull()).select(
        F.col("reject_reason").alias("reason"),
        "station_id",
        "event_time",
        "kafka_ts",
        F.substring("raw", 1, 1024).alias("raw"),
    )


def features(valid_df, window="30 seconds", watermark="15 seconds", delayed_s=10):
    delay = F.col("kafka_ts").cast("double") - F.col("event_time").cast("double")
    df = valid_df.withWatermark("event_time", watermark)
    # Within-watermark dedup only exists for streams; batch tests use the
    # plain equivalent, and a streaming test covers the real operator.
    df = (df.dropDuplicatesWithinWatermark(["station_id", "seq"]) if df.isStreaming
          else df.dropDuplicates(["station_id", "seq"]))
    return (
        df
        .withColumn("delay_s", delay)
        .groupBy(F.window("event_time", window), "station_id", "kind", "source", "scenario")
        .agg(
            F.count("*").alias("readings"),
            F.min("seq").alias("seq_min"),
            F.max("seq").alias("seq_max"),
            F.sum(F.when(F.col("delay_s") > delayed_s, 1).otherwise(0)).alias("delayed"),
            F.max("delay_s").alias("max_delay_s"),
            F.min("observed_at").alias("observed_from"),
            F.max("observed_at").alias("observed_to"),
            F.max("event_time").alias("last_event_at"),
            F.min("temperature_c").alias("temp_min"),
            F.avg("temperature_c").alias("temp_avg"),
            F.max("temperature_c").alias("temp_max"),
            F.avg("humidity_pct").alias("humidity_avg"),
            F.avg("rain_mmph").alias("rain_avg"),
            F.max("rain_mmph").alias("rain_max"),
            F.avg("wind_kmph").alias("wind_avg"),
            F.max("gust_kmph").alias("gust_max"),
            F.min("visibility_m").alias("visibility_min"),
            F.avg("pressure_hpa").alias("pressure_avg"),
            F.max("rain_24h_mm").alias("rain_24h"),
        )
        .select(
            "station_id", "kind", "source", "scenario",
            F.col("window.start").alias("window_start"),
            F.col("window.end").alias("window_end"),
            "observed_from", "observed_to", "last_event_at", "readings", "seq_min", "seq_max", "delayed", "max_delay_s",
            "temp_min", "temp_avg", "temp_max", "humidity_avg", "rain_avg", "rain_max", "wind_avg",
            "gust_max", "visibility_min", "pressure_avg", "rain_24h",
        )
    )
