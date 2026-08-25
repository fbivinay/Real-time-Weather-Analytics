# Databricks notebook source
dbutils.widgets.text("kafka_bootstrap", "")
dbutils.widgets.text("s3_bucket", "")

kafka_bootstrap = dbutils.widgets.get("kafka_bootstrap")
s3_bucket = dbutils.widgets.get("s3_bucket")

# COMMAND ----------

s3_access_key = dbutils.secrets.get("weather-pipeline", "s3-access-key")
s3_secret_key = dbutils.secrets.get("weather-pipeline", "s3-secret-key")

spark.conf.set("spark.hadoop.fs.s3a.access.key", s3_access_key)
spark.conf.set("spark.hadoop.fs.s3a.secret.key", s3_secret_key)
spark.conf.set("spark.hadoop.fs.s3a.endpoint", "s3.amazonaws.com")

# COMMAND ----------

from kafka.admin import KafkaAdminClient, NewTopic
from kafka.errors import TopicAlreadyExistsError

admin = KafkaAdminClient(bootstrap_servers=kafka_bootstrap)
try:
    admin.create_topics(
        [NewTopic(name="weather-processed", num_partitions=1, replication_factor=1)]
    )
except TopicAlreadyExistsError:
    pass
admin.close()

# COMMAND ----------

from pyspark.sql.functions import (
    col, from_json, to_json, struct, lit, when, avg, min as spark_min,
    max as spark_max, count, window, date_format,
)
from pyspark.sql.types import (
    StructType, StructField, StringType, DoubleType, IntegerType, TimestampType,
)

from transforms import ALERT_THRESHOLDS

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
    .option("kafka.bootstrap.servers", kafka_bootstrap)
    .option("subscribe", "weather-data")
    .option("startingOffsets", "latest")
    .load()
)

parsed_df = (
    raw_kafka_df
    .select(from_json(col("value").cast("string"), READING_SCHEMA).alias("data"))
    .select("data.*")
)

# COMMAND ----------

CHECKPOINT_ROOT = "/checkpoints/weather-processing"


def write_kafka(df, name):
    return (
        df.select(to_json(struct("*")).alias("value"))
        .writeStream
        .format("kafka")
        .outputMode("append")
        .option("kafka.bootstrap.servers", kafka_bootstrap)
        .option("topic", "weather-processed")
        .option("checkpointLocation", f"{CHECKPOINT_ROOT}/{name}-kafka")
        .start()
    )


def write_s3(df, path, name):
    return (
        df.withColumn("date", date_format(col("timestamp"), "yyyy-MM-dd"))
        .writeStream
        .format("parquet")
        .outputMode("append")
        .option("path", f"s3a://{s3_bucket}/{path}")
        .option("checkpointLocation", f"{CHECKPOINT_ROOT}/{name}-s3")
        .partitionBy("date")
        .start()
    )

# COMMAND ----------

raw_query = write_s3(parsed_df, "raw", "raw")

# COMMAND ----------

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

# COMMAND ----------

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

# COMMAND ----------

queries = [raw_query, alert_kafka_query, alert_s3_query, agg_kafka_query, agg_s3_query]

spark.streams.awaitAnyTermination(timeout=300_000)

for query in queries:
    query.stop()
