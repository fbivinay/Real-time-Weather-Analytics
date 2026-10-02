"""DataFrame -> DataFrame steps of the streaming job, kept free of sources
and sinks so the same code runs as a stream in production and as a batch
job in tests. Python 3.8 (Spark image): no 3.9+ syntax here."""
from pyspark.sql import functions as F
from pyspark.sql.types import StringType, StructField, StructType, TimestampType

from weatherops.events import FUTURE_TOLERANCE_S, NEEDS_CITY, NEEDS_ORDER, TYPES

EVENT_SCHEMA = StructType([
    StructField("event_id", StringType()),
    StructField("type", StringType()),
    StructField("sim_time", TimestampType()),
    StructField("emitted_at", TimestampType()),
    StructField("order_id", StringType()),
    StructField("city_id", StringType()),
    StructField("route_id", StringType()),
    StructField("payload", StringType()),
])
CLEAN_COLUMNS = ("event_id", "type", "sim_time", "emitted_at", "order_id", "city_id", "route_id", "payload",
                 "kafka_ts")


def parse(kafka_df):
    raw = F.col("value").cast("string")
    return (
        kafka_df.select(raw.alias("raw"), F.col("timestamp").alias("kafka_ts"),
                        F.from_json(raw, EVENT_SCHEMA).alias("e"))
        .select("raw", "kafka_ts", "e.*")
    )


def reject_reason():
    """Same rule order as weatherops.events.validate(); null = valid."""
    blank = F.col("event_id").isNull() | (F.col("event_id") == "")
    future = F.current_timestamp() + F.expr("INTERVAL {} SECONDS".format(FUTURE_TOLERANCE_S))
    return (
        F.when(blank | F.col("sim_time").isNull() | F.col("emitted_at").isNull(), F.lit("unparseable"))
        .when(F.col("type").isNull() | ~F.col("type").isin(*TYPES), F.lit("unknown_type"))
        .when(F.col("type").isin(*NEEDS_ORDER) & F.col("order_id").isNull(), F.lit("missing_order_id"))
        .when(F.col("type").isin(*NEEDS_CITY) & F.col("city_id").isNull(), F.lit("missing_city_id"))
        .when(F.col("emitted_at") > future, F.lit("future_timestamp"))
    )


def with_reject_reason(df):
    return df.withColumn("reject_reason", reject_reason())


def quarantine(df):
    return df.filter(F.col("reject_reason").isNotNull()).select(
        F.col("reject_reason").alias("reason"), "event_id", "type", "kafka_ts",
        F.substring("raw", 1, 1024).alias("raw"),
    )


def clean(df, watermark="30 seconds"):
    """Valid events, each event_id once. The watermark runs on emitted_at (wall
    clock): simulated time moves 60x and backfills in bursts."""
    valid = df.filter(F.col("reject_reason").isNull()).withWatermark("emitted_at", watermark)
    # Within-watermark dedup only exists for streams; batch tests use the
    # plain equivalent, and a streaming test covers the real operator.
    valid = (valid.dropDuplicatesWithinWatermark(["event_id"]) if valid.isStreaming
             else valid.dropDuplicates(["event_id"]))
    return valid.select(*CLEAN_COLUMNS)


def metrics(clean_df, window="30 seconds"):
    """Per city, per 30 s of wall-clock time: what the operation did."""
    is_type = lambda t: F.when(F.col("type") == t, 1).otherwise(0)  # noqa: E731
    delay = F.when(F.col("type") == "DELIVERY_COMPLETED",
                   F.get_json_object("payload", "$.delay_min").cast("double"))
    return (
        clean_df.groupBy(F.window("emitted_at", window), F.coalesce("city_id", F.lit("ALL")).alias("city_id"))
        .agg(
            F.count("*").alias("events"),
            F.sum(is_type("ORDER_CREATED")).alias("created"),
            F.sum(is_type("VEHICLE_DISPATCHED")).alias("dispatched"),
            F.sum(is_type("DELIVERY_COMPLETED")).alias("completed"),
            F.sum(is_type("DELIVERY_DELAY")).alias("delays"),
            F.avg(delay).alias("avg_delay_min"),
            F.max("sim_time").alias("max_sim_time"),
        )
        .select(F.col("window.start").alias("window_start"), F.col("window.end").alias("window_end"),
                "city_id", "events", "created", "dispatched", "completed", "delays", "avg_delay_min",
                "max_sim_time")
    )
