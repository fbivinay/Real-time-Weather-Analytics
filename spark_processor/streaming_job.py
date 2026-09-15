import os

from pyspark.sql import SparkSession
from pyspark.sql.functions import (
    avg, col, count, date_format, from_json, lit, max as spark_max,
    min as spark_min, struct, to_json, when, window,
)
from pyspark.sql.types import (
    DoubleType, IntegerType, StringType, StructField, StructType, TimestampType,
)

from transforms import ALERT_THRESHOLDS

KAFKA_BOOTSTRAP = os.environ.get(
    "KAFKA_BOOTSTRAP", "kafka.weather-pipeline.svc.cluster.local:9092"
)
SOURCE_TOPIC = os.environ.get("SOURCE_TOPIC", "weather-data")
SINK_TOPIC = os.environ.get("SINK_TOPIC", "weather-processed")
S3_BUCKET = os.environ["S3_BUCKET"]
CHECKPOINT_ROOT = os.environ.get("CHECKPOINT_ROOT", "/checkpoints/weather-processing")

spark = (
    SparkSession.builder
    .appName("weather-processing")
    # S3A reads AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY from the pod env,
    # so no credential ever lands in Spark conf or the Spark UI.
    .config(
        "spark.hadoop.fs.s3a.aws.credentials.provider",
        "com.amazonaws.auth.EnvironmentVariableCredentialsProvider",
    )
    .config("spark.hadoop.fs.s3a.impl", "org.apache.hadoop.fs.s3a.S3AFileSystem")
    .config("spark.sql.streaming.metricsEnabled", "true")
    .getOrCreate()
)
spark.sparkContext.setLogLevel("WARN")

READING_SCHEMA = StructType([
    StructField("station_id", StringType()),
    StructField("city", StringType()),
    StructField("timestamp", TimestampType()),
    StructField("temperature", DoubleType()),
    StructField("humidity", IntegerType()),
    StructField("rainfall", DoubleType()),
    StructField("wind_speed", DoubleType()),
])

raw_kafka_df = (
    spark.readStream.format("kafka")
    .option("kafka.bootstrap.servers", KAFKA_BOOTSTRAP)
    .option("subscribe", SOURCE_TOPIC)
    .option("startingOffsets", "latest")
    .load()
)

parsed_df = (
    raw_kafka_df
    .select(from_json(col("value").cast("string"), READING_SCHEMA).alias("data"))
    .select("data.*")
)


def write_kafka(df, name):
    return (
        df.select(to_json(struct("*")).alias("value"))
        .writeStream
        .format("kafka")
        .outputMode("append")
        .option("kafka.bootstrap.servers", KAFKA_BOOTSTRAP)
        .option("topic", SINK_TOPIC)
        .option("checkpointLocation", f"{CHECKPOINT_ROOT}/{name}-kafka")
        .start()
    )


def write_s3(df, path, name):
    return (
        df.withColumn("date", date_format(col("timestamp"), "yyyy-MM-dd"))
        .writeStream
        .format("parquet")
        .outputMode("append")
        .option("path", f"s3a://{S3_BUCKET}/{path}")
        .option("checkpointLocation", f"{CHECKPOINT_ROOT}/{name}-s3")
        .partitionBy("date")
        .start()
    )


raw_query = write_s3(parsed_df, "raw", "raw")

alert_types = list(ALERT_THRESHOLDS.items())

alert_type_col = when(
    col(alert_types[0][1]["field"]) > alert_types[0][1]["threshold"], alert_types[0][0]
)
for name, rule in alert_types[1:]:
    alert_type_col = alert_type_col.when(col(rule["field"]) > rule["threshold"], name)
alert_type_col = alert_type_col.otherwise(None)

field_col = when(col("alert_type") == alert_types[0][0], lit(alert_types[0][1]["field"]))
for name, rule in alert_types[1:]:
    field_col = field_col.when(col("alert_type") == name, lit(rule["field"]))

threshold_col = when(col("alert_type") == alert_types[0][0], lit(alert_types[0][1]["threshold"]))
for name, rule in alert_types[1:]:
    threshold_col = threshold_col.when(col("alert_type") == name, lit(rule["threshold"]))

value_col = when(col("alert_type") == alert_types[0][0], col(alert_types[0][1]["field"]))
for name, rule in alert_types[1:]:
    value_col = value_col.when(col("alert_type") == name, col(rule["field"]))

alerts_df = (
    parsed_df
    .withColumn("alert_type", alert_type_col)
    .filter(col("alert_type").isNotNull())
    .withColumn("field", field_col)
    .withColumn("threshold", threshold_col)
    .withColumn("value", value_col)
    .withColumn("record_type", lit("alert"))
    .select("record_type", "station_id", "city", "timestamp", "alert_type", "field", "value", "threshold")
)

alert_kafka_query = write_kafka(alerts_df, "alerts")
alert_s3_query = write_s3(alerts_df, "alerts", "alerts")

aggregates_df = (
    parsed_df
    .withWatermark("timestamp", "2 minutes")
    .groupBy(window(col("timestamp"), "1 minute"), col("station_id"), col("city"))
    .agg(
        avg("temperature").alias("avg_temperature"),
        spark_min("temperature").alias("min_temperature"),
        spark_max("temperature").alias("max_temperature"),
        avg("humidity").alias("avg_humidity"),
        avg("rainfall").alias("avg_rainfall"),
        avg("wind_speed").alias("avg_wind_speed"),
        count("*").alias("reading_count"),
    )
    .select(
        lit("aggregate").alias("record_type"),
        col("station_id"),
        col("city"),
        col("window.start").alias("window_start"),
        col("window.end").alias("window_end"),
        col("avg_temperature"), col("min_temperature"), col("max_temperature"),
        col("avg_humidity"), col("avg_rainfall"), col("avg_wind_speed"),
        col("reading_count"),
    )
)

agg_kafka_query = write_kafka(aggregates_df, "aggregates")
agg_s3_query = write_s3(
    aggregates_df.withColumnRenamed("window_start", "timestamp"), "aggregates", "aggregates"
)

# Unlike the Databricks scheduled-Job variant, this runs as a long-lived
# Deployment: no timeout, restarted by Kubernetes if it dies.
spark.streams.awaitAnyTermination()
