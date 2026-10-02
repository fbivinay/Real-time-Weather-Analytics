"""WeatherOps stream processor for ShopFlow operations events.

One Kafka read per query (Spark does not share sources between queries):

  shopflow-events ─┬─ raw ───────────────────────────────▶ S3 raw/ (when S3_BUCKET is set)
                   ├─ invalid ─▶ shopflow-quarantine
                   ├─ valid → dedup ─▶ shopflow-clean + Postgres delivery_events
                   └─ valid → dedup → 30 s city windows ─▶ shopflow-metrics + Postgres stream_metrics
"""
import os

from pyspark.sql import SparkSession
from pyspark.sql import functions as F

from spark_processor import plans
from spark_processor.listener import make_listener

KAFKA_BOOTSTRAP = os.environ.get("KAFKA_BOOTSTRAP", "kafka.weather-pipeline.svc.cluster.local:9092")
S3_BUCKET = os.environ.get("S3_BUCKET")
CHECKPOINT_ROOT = os.environ.get("CHECKPOINT_ROOT", "/checkpoints/shopflow")
PG_URL = "jdbc:postgresql://{}:{}/{}".format(os.environ.get("PGHOST", "postgres"), os.environ.get("PGPORT", "5432"),
                                             os.environ.get("PGDATABASE", "weatherops"))
PG_PROPS = {"user": os.environ.get("PGUSER", "weatherops"), "password": os.environ.get("PGPASSWORD", ""),
            "driver": "org.postgresql.Driver", "stringtype": "unspecified"}
FAST = "5 seconds"

spark = (
    SparkSession.builder
    .appName("weatherops-processor")
    # S3A reads AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY from the pod env,
    # so no credential ever lands in Spark conf or the Spark UI.
    .config("spark.hadoop.fs.s3a.aws.credentials.provider",
            "com.amazonaws.auth.EnvironmentVariableCredentialsProvider")
    .config("spark.hadoop.fs.s3a.impl", "org.apache.hadoop.fs.s3a.S3AFileSystem")
    .config("spark.sql.session.timeZone", "UTC")
    # A handful of state stores is plenty for ~60 events/s on two cores.
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


def events_stream():
    return (
        spark.readStream.format("kafka")
        .option("kafka.bootstrap.servers", KAFKA_BOOTSTRAP)
        .option("subscribe", "shopflow-events")
        .option("startingOffsets", "latest")
        # Kafka keeps 24 h and the node is torn down between sessions; losing
        # expired offsets must not stop the stream.
        .option("failOnDataLoss", "false")
        .option("maxOffsetsPerTrigger", 50000)
        .load()
    )


def checked():
    return plans.with_reject_reason(plans.parse(events_stream()))


def to_kafka_batch(df, topic):
    (df.select(F.to_json(F.struct("*")).alias("value"))
     .write.format("kafka").option("kafka.bootstrap.servers", KAFKA_BOOTSTRAP).option("topic", topic).save())


def sink_both(topic, table):
    def write(batch_df, _batch_id):
        batch_df.persist()
        try:
            if batch_df.take(1):
                to_kafka_batch(batch_df, topic)
                batch_df.write.jdbc(PG_URL, table, mode="append", properties=PG_PROPS)
        finally:
            batch_df.unpersist()
    return write


def start(df, name, writer, mode="append"):
    return (df.writeStream.queryName(name).outputMode(mode).foreachBatch(writer)
            .option("checkpointLocation", "{}/{}".format(CHECKPOINT_ROOT, name))
            .trigger(processingTime=FAST).start())


if S3_BUCKET:
    (checked().drop("raw").withColumn("date", F.date_format("kafka_ts", "yyyy-MM-dd"))
     .writeStream.queryName("raw-s3").format("parquet").outputMode("append")
     .option("path", "s3a://{}/shopflow/raw".format(S3_BUCKET))
     .option("checkpointLocation", "{}/raw-s3".format(CHECKPOINT_ROOT))
     .partitionBy("date").trigger(processingTime="60 seconds").start())

(plans.quarantine(checked()).select(F.to_json(F.struct("*")).alias("value"))
 .writeStream.queryName("quarantine").format("kafka").outputMode("append")
 .option("kafka.bootstrap.servers", KAFKA_BOOTSTRAP).option("topic", "shopflow-quarantine")
 .option("checkpointLocation", "{}/quarantine".format(CHECKPOINT_ROOT))
 .trigger(processingTime=FAST).start())

start(plans.clean(checked()), "clean", sink_both("shopflow-clean", "delivery_events"))
start(plans.metrics(plans.clean(checked())), "metrics", sink_both("shopflow-metrics", "stream_metrics"))

# Long-lived Deployment: no timeout, Kubernetes restarts it if a query dies.
spark.streams.awaitAnyTermination()
