# Databricks Structured Streaming — Alerts + Aggregates (sub-project 3 of 5)

## Context

Part of the Real-Time Weather Analytics Pipeline. Sub-project 1 (infra)
stood up EC2 + k3s + Kafka (KRaft, external listener at
`<elastic-ip>:9094`, PLAINTEXT, no auth). Sub-project 2 (data pipeline)
produces realistic weather readings, with occasional extreme values, to
the `weather-data` topic.

Full data flow (this sub-project is the middle stage):

```
Kafka topic: weather-data                    (sub-project 2)
      │
      ▼
Databricks Structured Streaming (this sub-project)
      │
      ├──▶ Kafka topic: weather-processed    (alerts + aggregates)
      │
      └──▶ S3 (raw + alerts + aggregates, parallel history)
                   │
                   ▼
      Redis → FastAPI → Next.js dashboard    (sub-project 4)
```

This sub-project owns and creates `weather-processed`, the same way
sub-project 2 owns `weather-data` — infra deliberately does not create
application topics.

**Platform:** Databricks Community Edition (free tier). This drives
several constraints handled explicitly in this design rather than
ignored:

- Clusters auto-terminate after idle time — no always-on streaming job.
  This sub-project runs as a periodically **scheduled Job**, not a
  continuous service.
- No IAM instance-profile attachment — S3 access uses a plain AWS
  access key/secret, stored in a Databricks CE secret scope (not
  committed to git, not hardcoded in the notebook).
- Outbound internet access is available, so the CE cluster can reach
  the Kafka broker's public Elastic IP on port 9094 like any other
  external client.

## Goals

- Consume `weather-data`, detect alert conditions (heat, heavy rain,
  high wind — same thresholds sub-project 2's generator uses to inject
  extreme values), and compute 1-minute per-station aggregates
  (avg/min/max + count).
- Publish both alerts and aggregates to `weather-processed`, tagged
  with a `record_type` discriminator so downstream consumers can tell
  them apart in one topic.
- Write the same data (raw passthrough + aggregates + alerts) to S3 as
  Parquet, partitioned by date, as durable history independent of
  Kafka's 24h retention.
- Run on a schedule compatible with Databricks CE's auto-idle
  behavior, resuming cleanly between runs via checkpointed Kafka
  offsets (no gaps, no duplicates).

## Non-goals

- Always-on / continuous processing — CE cannot sustain this; a
  15-minute schedule with a bounded run duration is the deliberate
  choice, not a placeholder for something better later.
- Terraform-managing the Databricks Job itself — Databricks CE's Jobs
  API access is limited; the Job is set up manually by the operator,
  documented as a runbook step.
- Consuming `weather-processed` — that's sub-project 4.
- Any per-city climate modeling, alert deduplication/suppression, or
  alerting integrations (email/Slack/etc.) — out of scope for a demo
  pipeline; alerts are just tagged records in the topic and in S3.
- Unity Catalog, Delta Lake tables, or any Databricks feature beyond
  what Community Edition provides — plain Parquet on S3 is sufficient.

## Design

### Terraform additions (extends `infra/`)

- `aws_s3_bucket.weather_pipeline` — versioning off, tagged
  `Project = "weather-pipeline"`.
- `aws_iam_user.databricks_s3` — dedicated user, no console access.
- Inline IAM policy on that user: `s3:PutObject`, `s3:GetObject`,
  `s3:ListBucket`, scoped to `arn:aws:s3:::<bucket>` and
  `arn:aws:s3:::<bucket>/*` only — nothing broader.
- `aws_iam_access_key.databricks_s3` — produces `access_key_id` and a
  sensitive `secret_access_key` output. Never printed in plain
  `terraform output`; read via `terraform output -raw
  databricks_s3_secret_key` when setting up the Databricks secret
  scope, same handling discipline as the SSH key (never touches git).

### Databricks notebook / job structure

**Widgets (job parameters):** `kafka_bootstrap` (e.g.
`<elastic-ip>:9094`), `s3_bucket`.

**Credentials:** AWS access key/secret stored in a Databricks CE
secret scope (`databricks secrets create-scope weather-pipeline`,
`databricks secrets put-secret weather-pipeline s3-access-key`, etc.,
via the Databricks CLI — an operator setup step, documented in the
runbook, not code in this repo). Read at runtime with
`dbutils.secrets.get("weather-pipeline", "s3-access-key")` and set as
`spark.conf.set("spark.hadoop.fs.s3a.access.key", ...)` /
`.secret.key`. Kafka itself has no auth (PLAINTEXT listener) — only
`kafka_bootstrap` is needed, passed as a plain widget, not a secret.

**One `readStream`, three branches:**

```
raw_df = spark.readStream.format("kafka")
    .option("kafka.bootstrap.servers", kafka_bootstrap)
    .option("subscribe", "weather-data")
    .option("startingOffsets", "latest")   # first run only; checkpoint drives subsequent runs
    .load()
    # parse JSON value into columns: station_id, city, timestamp, temperature, humidity, rainfall, wind_speed
```

1. **Raw branch** — passthrough of parsed `weather-data` rows, no
   transformation. Sink: S3 only (`raw/`) — not written back to
   `weather-processed` (that topic is for alerts + aggregates only, per
   the Goals).
2. **Alert branch** — filter where `temperature > 40` OR
   `rainfall > 50` OR `wind_speed > 60`; project into the alert record
   shape (one row per triggering reading — a reading can trigger at
   most one alert since the generator only pushes one field extreme
   per cycle). Sinks: `weather-processed` (Kafka) and S3 (`alerts/`).
3. **Aggregate branch** — `groupBy(window(timestamp, "1 minute"),
   station_id, city)`, `avg`/`min`/`max` on the four numeric fields
   plus `count(*)` as `reading_count`. Sinks: `weather-processed`
   (Kafka) and S3 (`aggregates/`).

Each of the 3 branches has 2 sinks (Kafka + S3) — 6 `writeStream`
queries total, each with its own checkpoint directory under DBFS
(`/checkpoints/weather-processing/<branch>-<sink>/`). DBFS is durable
across cluster restarts within the CE workspace, so checkpoints survive
between scheduled runs even though the cluster itself terminates.

**Run control:** after starting all 6 queries,
`spark.streams.awaitAnyTermination(timeout=300_000)` (5 minutes), then
explicitly `.stop()` every query, then the notebook returns. The Job is
scheduled every 15 minutes — each run processes 5 minutes actively,
catches up on the ~10 minutes of buffered `weather-data` messages
(sitting in Kafka's 24h retention) quickly since Spark reads
checkpointed offsets forward, not from `latest` again.

**Message schema — `weather-processed`** (JSON):

```json
// alert record
{
  "record_type": "alert",
  "station_id": "ST001",
  "city": "Bengaluru",
  "timestamp": "2026-08-25T10:20:23Z",
  "alert_type": "heat",
  "field": "temperature",
  "value": 44.3,
  "threshold": 40
}

// aggregate record
{
  "record_type": "aggregate",
  "station_id": "ST001",
  "city": "Bengaluru",
  "window_start": "2026-08-25T10:20:00Z",
  "window_end": "2026-08-25T10:21:00Z",
  "avg_temperature": 24.1,
  "min_temperature": 18.0,
  "max_temperature": 31.2,
  "avg_humidity": 61.4,
  "avg_rainfall": 2.8,
  "avg_wind_speed": 15.9,
  "reading_count": 12
}
```

`alert_type` → `field` → `threshold` mapping (matches sub-project 2's
extreme-value ranges exactly, so every extreme reading the generator
produces is guaranteed to trigger the corresponding alert):

| `alert_type` | `field`       | `threshold` |
|---|---|---|
| `heat`       | `temperature` | 40          |
| `heavy_rain` | `rainfall`    | 50          |
| `high_wind`  | `wind_speed`  | 60          |

**Topic creation:** the job creates `weather-processed` on first write
if missing (`kafka-python`'s `KafkaAdminClient` from the driver, same
idempotent-create pattern as sub-project 2, run once before starting
the streaming queries) — `num_partitions=1`, `replication_factor=1`,
matching the single-broker cluster.

**S3 layout** (Parquet, partitioned by `date=YYYY-MM-DD` derived from
each row's `timestamp`):

```
s3://<bucket>/raw/date=.../part-*.parquet
s3://<bucket>/aggregates/date=.../part-*.parquet
s3://<bucket>/alerts/date=.../part-*.parquet
```

## Files

- `infra/main.tf` — append `aws_s3_bucket`, `aws_iam_user`,
  `aws_iam_user_policy`, `aws_iam_access_key` resources.
- `infra/outputs.tf` — append `s3_bucket_name`, `databricks_s3_access_key_id`,
  sensitive `databricks_s3_secret_key`.
- `databricks/transforms.py` — pure functions: `check_alert(row) ->
  dict | None` (the three-threshold check), alert threshold constants.
  No Spark/Databricks dependency — plain Python, unit-testable.
- `databricks/weather_processing.py` — the Structured Streaming
  notebook source (Databricks `# Databricks notebook source` header
  format): widgets, secret scope reads, `readStream`, the 3 branches,
  6 `writeStream` sinks, run-control logic. Imports `check_alert` and
  the threshold constants from `transforms.py` for the alert branch's
  filter condition, so the single source of truth for thresholds lives
  in the tested module.
- `databricks/tests/test_transforms.py` — unit tests for `check_alert`
  covering: no alert (normal ranges), each of the three alert types
  triggering, and the boundary condition (exactly at threshold does
  NOT alert, matching sub-project 2's `>` not `>=` semantics).
- `databricks/README.md` — operator runbook: CE workspace setup,
  `databricks secrets` CLI commands for the S3 credentials, uploading
  `weather_processing.py` as a notebook, creating the scheduled Job
  (15-min interval, widget values), and how to read the Terraform
  outputs (`s3_bucket_name`, `databricks_s3_access_key_id`,
  `databricks_s3_secret_key`) to fill in the secret scope.

## Deployment

1. `cd infra && terraform apply` — provisions the S3 bucket and IAM
   user/key (extends the already-applied sub-project 1 infra; existing
   EC2/Kafka/Redis resources are untouched).
2. `terraform output s3_bucket_name` / `databricks_s3_access_key_id` /
   `terraform output -raw databricks_s3_secret_key` — record these.
3. In the Databricks CE workspace: install the CLI, run `databricks
   secrets create-scope weather-pipeline` and `put-secret` for the
   access key and secret key (exact commands in
   `databricks/README.md`).
4. Import `databricks/weather_processing.py` as a notebook.
5. Create a Job: single task running that notebook, schedule every 15
   minutes, widget values `kafka_bootstrap = <elastic-ip>:9094` (from
   sub-project 1's `terraform output kafka_bootstrap`) and
   `s3_bucket = <bucket-name>`.
6. Run the Job once manually to verify before leaving it on the
   schedule.

## Verification (definition of done for this sub-project)

1. `terraform apply` succeeds; bucket and IAM user/key exist.
2. `databricks/tests/test_transforms.py` passes locally (no cluster
   needed).
3. A manual Job run completes without error within the 5-minute
   window.
4. `weather-processed` topic exists and contains both `record_type:
   alert` and `record_type: aggregate` messages, matching the
   documented JSON shapes (verified via `kubectl exec` into the Kafka
   broker pod, same pattern sub-projects 1-2 used).
5. `aws s3 ls s3://<bucket>/raw/`, `.../aggregates/`, `.../alerts/`
   each show Parquet objects for the current date after a run.
6. At least one alert record is observed within a couple of scheduled
   runs (statistically near-certain given sub-project 2's ~1-in-30
   extreme-value rate observed live during its own verification).
7. Re-running the Job a second time does not reprocess already-seen
   `weather-data` offsets (confirmed by comparing `reading_count`
   totals in `weather-processed`/S3 against what a fresh
   `--from-beginning` consume of `weather-data` would show — no
   duplication).

## Open follow-ups (tracked, not blocking)

- 15-minute schedule / 5-minute run window are reasonable defaults for
  a demo, not tuned against any real latency requirement — revisit if
  sub-project 4's dashboard needs fresher data than this cadence
  provides.
- No alert deduplication — if a station stays extreme across multiple
  readings, each one produces its own alert record. Acceptable for a
  demo; a real system would likely debounce.
- Community Edition's manual Job setup (not Terraform-managed) is a
  gap versus the rest of this project's "everything as code" pattern —
  acceptable trade-off given CE's Jobs API limitations, documented
  clearly in `databricks/README.md` so it's not a silent exception.
