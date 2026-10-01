"""WeatherOps stream processor: validation, quarantine, deduplication,
late-data handling and 30-second feature windows.

One Kafka read per query (Spark does not share sources between queries),
six queries in all:

  weather-data ─┬─ raw ──────────────────────────────▶ S3 raw/
                ├─ invalid ─▶ weather-quarantine ─────▶ S3 quarantine/
                └─ valid → dedup → 30 s windows ─▶ weather-features ─▶ S3 features/
  weather-decisions ────────────────────────────────▶ S3 decisions/
"""
import os

from pyspark.sql import SparkSession
from pyspark.sql import functions as F

from spark_processor import plans
from spark_processor.listener import make_listener

KAFKA_BOOTSTRAP = os.environ.get("KAFKA_BOOTSTRAP", "kafka.weather-pipeline.svc.cluster.local:9092")
S3_BUCKET = os.environ.get("S3_BUCKET")  # unset: no lake writes (local smoke runs)
CHECKPOINT_ROOT = os.environ.get("CHECKPOINT_ROOT", "/checkpoints/weatherops")
FAST, SLOW = "5 seconds", "60 seconds"  # Kafka sinks feed the engine; S3 sinks batch to limit small files

spark = (
    SparkSession.builder
    .appName("weatherops-processor")
    # S3A reads AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY from the pod env,
    # so no credential ever lands in Spark conf or the Spark UI.
    .config("spark.hadoop.fs.s3a.aws.credentials.provider",
            "com.amazonaws.auth.EnvironmentVariableCredentialsProvider")
    .config("spark.hadoop.fs.s3a.impl", "org.apache.hadoop.fs.s3a.S3AFileSystem")
    .config("spark.sql.session.timeZone", "UTC")
    # 200 shuffle partitions means 200 state stores per stateful operator; on
    # a 2-core node that is pure scheduling overhead for ~160 stations.
    .config("spark.sql.shuffle.partitions", "2")
    .getOrCreate()
)
spark.sparkContext.setLogLevel("WARN")

if os.environ.get("REDIS_HOST"):
    import redis

    spark.streams.addListener(make_listener(redis.Redis(
        host=os.environ["REDIS_HOST"], port=6379, password=os.environ.get("REDIS_PASSWORD"),
        decode_responses=True, socket_timeout=5,
    )))


def kafka_stream(topic):
    return (
        spark.readStream.format("kafka")
        .option("kafka.bootstrap.servers", KAFKA_BOOTSTRAP)
        .option("subscribe", topic)
        .option("startingOffsets", "latest")
        # Kafka keeps 24 h and the node is torn down between sessions; losing
        # expired offsets must not stop the stream.
        .option("failOnDataLoss", "false")
        .load()
    )


def to_kafka(df, topic, name):
    return (
        df.select(F.to_json(F.struct("*")).alias("value"))
        .writeStream.queryName(name).format("kafka").outputMode("append")
        .option("kafka.bootstrap.servers", KAFKA_BOOTSTRAP)
        .option("topic", topic)
        .option("checkpointLocation", f"{CHECKPOINT_ROOT}/{name}")
        .trigger(processingTime=FAST)
        .start()
    )


def to_s3(df, path, name, date_col):
    if not S3_BUCKET:
        return None
    return (
        df.withColumn("date", F.date_format(F.col(date_col), "yyyy-MM-dd"))
        .writeStream.queryName(name).format("parquet").outputMode("append")
        .option("path", f"s3a://{S3_BUCKET}/{path}")
        .option("checkpointLocation", f"{CHECKPOINT_ROOT}/{name}")
        .partitionBy("date")
        .trigger(processingTime=SLOW)
        .start()
    )


readings = plans.with_reject_reason(plans.parse(kafka_stream("weather-data")))
to_s3(readings.drop("raw"), "raw", "raw-s3", "kafka_ts")

quarantined = plans.quarantine(readings)
to_kafka(quarantined, "weather-quarantine", "quarantine-kafka")
to_s3(quarantined, "quarantine", "quarantine-s3", "kafka_ts")

features = plans.features(readings.filter(F.col("reject_reason").isNull()))
to_kafka(features, "weather-features", "features-kafka")
to_s3(features, "features", "features-s3", "window_start")

decisions = kafka_stream("weather-decisions").select(
    F.col("value").cast("string").alias("json"),
    F.get_json_object(F.col("value").cast("string"), "$.record_type").alias("record_type"),
    F.col("timestamp").alias("kafka_ts"),
)
to_s3(decisions, "decisions", "decisions-s3", "kafka_ts")

# Long-lived Deployment: no timeout, Kubernetes restarts it if a query dies.
spark.streams.awaitAnyTermination()
