# Databricks Structured Streaming — Alerts + Aggregates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Databricks Community Edition scheduled Job consumes
`weather-data`, detects heat/rain/wind alerts and computes 1-minute
per-station aggregates, and publishes both to `weather-processed`
(Kafka) and S3 (Parquet), resuming cleanly between runs via
checkpointed offsets.

**Architecture:** Terraform (extending sub-project 1's `infra/`)
provisions an S3 bucket and a scoped IAM user/key for Databricks. A
Databricks notebook (`databricks/weather_processing.py`) runs three
branches off one Kafka `readStream` — raw passthrough (S3 only), alert
detection (Kafka + S3), and windowed aggregation (Kafka + S3) — driven
by threshold constants shared with a unit-tested pure-Python module
(`databricks/transforms.py`). The Job itself is set up manually in the
CE workspace (documented in `databricks/README.md`), since CE's Jobs
API doesn't support Terraform management.

**Tech Stack:** Terraform (`hashicorp/aws` ~> 5.0), Databricks
Community Edition, PySpark Structured Streaming, `kafka-python-ng`
(topic creation), `pytest`.

**Spec:** `docs/superpowers/specs/2026-08-25-databricks-streaming-design.md`

## Global Constraints

- Databricks CE clusters auto-terminate on idle — no always-on job;
  the notebook runs a bounded 5-minute window per invocation, scheduled
  every 15 minutes (operator-configured, not in this repo's code).
- No IAM instance-profile attachment on CE — S3 access uses a plain
  AWS access key/secret via a Databricks CE secret scope, never
  hardcoded or committed.
- Kafka has no auth (PLAINTEXT external listener) — only the bootstrap
  address (`<elastic-ip>:9094`) is needed, passed as a widget.
- `weather-processed` carries only alerts and aggregates, tagged with
  `record_type` (`alert` | `aggregate`) — raw passthrough goes to S3
  only, not back to Kafka.
- Alert thresholds: `temperature > 40` → `heat`, `rainfall > 50` →
  `heavy_rain`, `wind_speed > 60` → `high_wind` — strict `>`, matching
  sub-project 2's generator semantics exactly.
- S3 IAM policy scoped to `s3:ListBucket`/`s3:PutObject`/`s3:GetObject`
  on the one bucket's ARN only — nothing broader.
- All new AWS resources tagged `Project = "weather-pipeline"`.

---

## Task 1: Terraform — S3 bucket + IAM user/policy/key for Databricks

**Files:**
- Modify: `infra/main.tf` (append S3 + IAM resources)
- Modify: `infra/outputs.tf` (append bucket/key outputs)

**Interfaces:**
- Consumes: nothing new — standalone addition to the existing
  provider/variables from sub-project 1.
- Produces: `aws_s3_bucket.weather_pipeline`,
  `aws_iam_access_key.databricks_s3` — consumed manually by the
  operator in Task 5 (reading `terraform output`), not by any other
  Terraform resource.

- [ ] **Step 1: Append the S3 bucket and IAM resources to `infra/main.tf`**

```hcl
data "aws_caller_identity" "current" {}

resource "aws_s3_bucket" "weather_pipeline" {
  bucket = "weather-pipeline-${data.aws_caller_identity.current.account_id}"

  tags = {
    Project = "weather-pipeline"
  }
}

resource "aws_iam_user" "databricks_s3" {
  name = "weather-pipeline-databricks-s3"

  tags = {
    Project = "weather-pipeline"
  }
}

resource "aws_iam_user_policy" "databricks_s3" {
  name = "weather-pipeline-databricks-s3-access"
  user = aws_iam_user.databricks_s3.name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ListBucket"
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = [aws_s3_bucket.weather_pipeline.arn]
      },
      {
        Sid      = "ReadWriteObjects"
        Effect   = "Allow"
        Action   = ["s3:PutObject", "s3:GetObject"]
        Resource = ["${aws_s3_bucket.weather_pipeline.arn}/*"]
      }
    ]
  })
}

resource "aws_iam_access_key" "databricks_s3" {
  user = aws_iam_user.databricks_s3.name
}
```

- [ ] **Step 2: Append outputs to `infra/outputs.tf`**

```hcl
output "s3_bucket_name" {
  description = "S3 bucket for weather pipeline history (raw/aggregates/alerts)"
  value       = aws_s3_bucket.weather_pipeline.bucket
}

output "databricks_s3_access_key_id" {
  description = "AWS access key ID for the Databricks S3 IAM user"
  value       = aws_iam_access_key.databricks_s3.id
}

output "databricks_s3_secret_key" {
  description = "AWS secret access key for the Databricks S3 IAM user"
  value       = aws_iam_access_key.databricks_s3.secret
  sensitive   = true
}
```

- [ ] **Step 3: Validate**

Run: `cd infra && terraform validate`
Expected: `Success! The configuration is valid.`

- [ ] **Step 4: Commit**

```bash
git add infra/main.tf infra/outputs.tf
git commit -m "infra: S3 bucket + scoped IAM user/key for Databricks S3 access"
```

---

## Task 2: `transforms.py` — pure alert-detection logic

**Files:**
- Create: `databricks/requirements-dev.txt`
- Create: `databricks/transforms.py`
- Test: `databricks/tests/test_transforms.py`

**Interfaces:**
- Produces: `ALERT_THRESHOLDS` (dict, `{alert_type: {"field": str,
  "threshold": int}}`) and `check_alert(reading: dict) -> dict | None`
  — `ALERT_THRESHOLDS` is imported by Task 3's notebook to build the
  matching Spark column expressions; `check_alert` is exercised only
  by this task's tests, as the executable definition of the alert
  logic the notebook must match.

- [ ] **Step 1: Write `databricks/requirements-dev.txt`**

```
pytest==8.3.4
```

- [ ] **Step 2: Write the failing tests**

```python
# databricks/tests/test_transforms.py
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from transforms import check_alert


def base_reading(**overrides):
    reading = {
        "station_id": "ST001",
        "city": "Bengaluru",
        "timestamp": "2026-08-25T10:20:23Z",
        "temperature": 25.0,
        "humidity": 60,
        "rainfall": 2.0,
        "wind_speed": 15.0,
    }
    reading.update(overrides)
    return reading


def test_no_alert_within_normal_ranges():
    assert check_alert(base_reading()) is None


def test_heat_alert():
    result = check_alert(base_reading(temperature=44.3))
    assert result == {
        "record_type": "alert",
        "station_id": "ST001",
        "city": "Bengaluru",
        "timestamp": "2026-08-25T10:20:23Z",
        "alert_type": "heat",
        "field": "temperature",
        "value": 44.3,
        "threshold": 40,
    }


def test_heavy_rain_alert():
    result = check_alert(base_reading(rainfall=72.0))
    assert result["alert_type"] == "heavy_rain"
    assert result["field"] == "rainfall"
    assert result["value"] == 72.0
    assert result["threshold"] == 50


def test_high_wind_alert():
    result = check_alert(base_reading(wind_speed=68.1))
    assert result["alert_type"] == "high_wind"
    assert result["field"] == "wind_speed"
    assert result["value"] == 68.1
    assert result["threshold"] == 60


def test_boundary_exactly_at_threshold_does_not_alert():
    assert check_alert(base_reading(temperature=40.0)) is None
    assert check_alert(base_reading(rainfall=50.0)) is None
    assert check_alert(base_reading(wind_speed=60.0)) is None
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd databricks && pip install -r requirements-dev.txt && pytest tests/test_transforms.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'transforms'`

- [ ] **Step 4: Write `databricks/transforms.py`**

```python
ALERT_THRESHOLDS = {
    "heat": {"field": "temperature", "threshold": 40},
    "heavy_rain": {"field": "rainfall", "threshold": 50},
    "high_wind": {"field": "wind_speed", "threshold": 60},
}


def check_alert(reading):
    for alert_type, rule in ALERT_THRESHOLDS.items():
        field = rule["field"]
        threshold = rule["threshold"]
        value = reading[field]
        if value > threshold:
            return {
                "record_type": "alert",
                "station_id": reading["station_id"],
                "city": reading["city"],
                "timestamp": reading["timestamp"],
                "alert_type": alert_type,
                "field": field,
                "value": value,
                "threshold": threshold,
            }
    return None
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd databricks && pytest tests/test_transforms.py -v`
Expected: 5 passed

- [ ] **Step 6: Commit**

```bash
git add databricks/requirements-dev.txt databricks/transforms.py databricks/tests/test_transforms.py
git commit -m "data-pipeline: alert-detection logic (transforms.py) with unit tests"
```

---

## Task 3: `weather_processing.py` — Structured Streaming notebook

**Files:**
- Create: `databricks/weather_processing.py`

**Interfaces:**
- Consumes: `ALERT_THRESHOLDS` (Task 2) — imported to build the alert
  branch's filter and derived columns.
- Consumes: `kafka_bootstrap` and `s3_bucket` widget values, and
  `weather-pipeline` secret scope keys `s3-access-key`/`s3-secret-key`
  — all supplied by the operator when the Job runs (Task 5).
- Produces: `weather-processed` Kafka topic records and
  `s3://<bucket>/{raw,aggregates,alerts}/date=.../` Parquet objects —
  consumed by sub-project 4 (not this plan).

No TDD here — this file needs a live Spark session and a reachable
Kafka broker, neither available outside Databricks. It's written whole
and verified live in Task 5, the same way sub-project 2's producer
Dockerfile was verified live rather than unit-tested.

- [ ] **Step 1: Write `databricks/weather_processing.py`**

```python
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
```

- [ ] **Step 2: Validate syntax**

Run: `cd databricks && python -m py_compile weather_processing.py`

`dbutils`/`spark` are Databricks-injected globals, undefined outside a
notebook context, so `py_compile` (syntax-only, no execution) is the
right check here — running the file directly would fail on those
names regardless of correctness.

Expected: no output, exit code 0.

- [ ] **Step 3: Commit**

```bash
git add databricks/weather_processing.py
git commit -m "data-pipeline: Structured Streaming notebook (alerts, aggregates, raw-to-S3)"
```

---

## Task 4: Operator runbook

**Files:**
- Create: `databricks/README.md`

**Interfaces:**
- Consumes: `terraform output s3_bucket_name`,
  `databricks_s3_access_key_id`, `databricks_s3_secret_key` (Task 1);
  `terraform output kafka_bootstrap` (sub-project 1).
- Produces: nothing consumed by other tasks — this is the human-facing
  setup guide for Task 5.

- [ ] **Step 1: Write `databricks/README.md`**

```markdown
# Databricks Structured Streaming — operator runbook

Community Edition workspace, manual setup (CE's Jobs API doesn't
support Terraform management).

## 1. Provision AWS resources

```bash
cd infra && terraform apply
terraform output s3_bucket_name
terraform output databricks_s3_access_key_id
terraform output -raw databricks_s3_secret_key
terraform output kafka_bootstrap
```

Record all four values.

## 2. Install the Databricks CLI and configure the secret scope

```bash
pip install databricks-cli
databricks configure --token   # paste your CE workspace URL + a personal access token
databricks secrets create-scope weather-pipeline
databricks secrets put-secret weather-pipeline s3-access-key --string-value "<databricks_s3_access_key_id>"
databricks secrets put-secret weather-pipeline s3-secret-key --string-value "<databricks_s3_secret_key>"
```

## 3. Import the notebook

In the Databricks CE workspace UI: Workspace → Import →
`databricks/weather_processing.py` (source format: Python), and import
`databricks/transforms.py` into the same folder so the notebook's
`from transforms import ALERT_THRESHOLDS` resolves — Databricks adds a
notebook's own folder to `sys.path` automatically.

## 4. Create the scheduled Job

Workflows → Create Job:
- Task: Notebook task pointing at the imported `weather_processing.py`.
- Parameters (widgets): `kafka_bootstrap` = `<value from step 1>`,
  `s3_bucket` = `<value from step 1>`.
- Schedule: every 15 minutes.
- Cluster: a new job cluster, default CE instance size (single node —
  CE only offers single-node clusters).

## 5. Run once manually before leaving it on the schedule

Workflows → this Job → Run now. Watch the run; it should complete
within the 5-minute window without error.

## 6. Verify

See the plan's Task 5 for the full verification checklist (topic
contents, S3 objects, no-reprocessing check on a second run).
```

- [ ] **Step 2: Commit**

```bash
git add databricks/README.md
git commit -m "data-pipeline: Databricks CE operator runbook"
```

---

## Task 5: Live setup, deploy, and verify against the real cluster + Databricks CE

No new code — this task is the integration test: apply Terraform,
follow the runbook to stand up the Job for real, and confirm the whole
pipeline works end-to-end. Needs sub-project 1's cluster reachable
(already up per its own plan) and a Databricks CE account.

**Files:** none created; this task only runs commands and follows
`databricks/README.md`.

**Interfaces:**
- Consumes: everything from Tasks 1-4, plus the live `weather-data`
  topic sub-project 2 is producing to.

- [ ] **Step 1: Apply Terraform**

Run: `cd infra && terraform apply` — confirm when prompted.
Expected: `aws_s3_bucket.weather_pipeline`,
`aws_iam_user.databricks_s3`, `aws_iam_user_policy.databricks_s3`,
`aws_iam_access_key.databricks_s3` created; existing sub-project 1
resources untouched.

- [ ] **Step 2: Follow `databricks/README.md` steps 2-5**

Set up the CLI, secret scope, import the notebook + `transforms.py`,
create the scheduled Job, run it once manually.
Expected: the manual run completes within 5 minutes, no errors in the
Job run's driver log.

- [ ] **Step 3: Confirm `weather-processed` contains both record types**

Run (via the SSH tunnel to the k3s cluster, same pattern sub-projects
1-2 used):
```bash
kubectl exec kafka-controller-0 -n weather-pipeline -- bash -c \
  "kafka-console-consumer.sh --bootstrap-server localhost:9092 --topic weather-processed --from-beginning --max-messages 20"
```
Expected: at least one `"record_type": "aggregate"` message with the
documented aggregate shape, and — if the run window overlapped an
extreme reading from sub-project 2 — at least one `"record_type":
"alert"` message with the documented alert shape.

- [ ] **Step 4: Confirm S3 objects exist**

Run: `aws s3 ls s3://$(cd infra && terraform output -raw s3_bucket_name)/raw/ --recursive`
Repeat for `aggregates/` and `alerts/`.
Expected: `.parquet` objects under a `date=YYYY-MM-DD/` prefix for
today's date in `raw/` and `aggregates/`; `alerts/` populated once an
extreme reading has occurred in a processed window.

- [ ] **Step 5: Confirm no reprocessing on a second run**

Note the `reading_count` sum across `weather-processed`'s aggregate
records (or count `raw/` Parquet rows) after Step 3. Trigger the Job
again (Workflows → Run now), wait for it to finish, then re-check.
Expected: the second run's new records cover only readings produced
*after* the first run ended — no duplicate `station_id` +
`window_start` pairs in the aggregates, confirming the DBFS checkpoint
correctly resumed from the first run's offsets rather than restarting
from `latest` or re-reading from the beginning.

- [ ] **Step 6: No commit** — this task is verification-only; nothing
  new to commit unless Steps 1-5 revealed a bug requiring a code fix,
  in which case fix it, re-run the relevant steps, and commit the fix
  with a message describing what broke and why.

---

## Self-Review Notes

- Spec coverage: S3 bucket + IAM (Task 1), `transforms.py`/tests
  (Task 2), the notebook's 3 branches / 5 sinks / checkpointing / run
  control (Task 3), operator runbook (Task 4), live end-to-end
  verification including the no-duplication check (Task 5) — all
  spec sections covered.
- Deviation from the spec's literal wording flagged inline in Task 3:
  the alert branch imports `ALERT_THRESHOLDS` and builds Spark column
  expressions from it, rather than calling `check_alert` as a UDF —
  avoids UDF timestamp-serialization fragility in a streaming context
  while keeping the threshold values as the single source of truth.
  The spec document itself was corrected during planning to match
  (5 writeStream queries, not 6 — raw branch is S3-only).
- No placeholders: every code block is complete and transcribable as
  written, including the runbook's exact CLI commands.
