# Real-Time Weather Analytics Pipeline

Five simulated weather stations emit readings every five seconds into Kafka on a
single-node Kubernetes cluster in AWS. A Spark Structured Streaming job turns
them into threshold alerts and one-minute per-station aggregates, writing both
back to Kafka and to S3 as Parquet. A Redis-backed API serves the current state
to a Next.js dashboard.

```
weather-generator ──▶ Kafka: weather-data
                            │
                            ▼
              Spark Structured Streaming
                   │              │
                   ▼              ▼
    Kafka: weather-processed     S3 (raw / alerts / aggregates)
                   │
                   ▼
        consumer ──▶ Redis ──▶ FastAPI ──▶ Next.js dashboard
```

## Run it

```bash
./deploy.sh            # bring everything up, in order
./deploy.sh --status   # what is running
./deploy.sh --destroy  # tear down and stop billing
```

The node bills by the hour. Destroy it when you are done.

The dashboard deploys separately, since it runs on Vercel rather than in the
cluster:

```bash
cd dashboard && vercel deploy --prod
```

## Layout

| Path | What it is |
|---|---|
| `infra/` | Terraform: EC2, k3s, security group, S3, IAM. `bootstrap.sh` installs Kafka and Redis via Helm. |
| `producer/` | The station simulator. Pure generation logic is separate from the Kafka client so it can be tested without a broker. |
| `spark_processor/` | The streaming job: one Kafka read stream, three branches, five sinks. Runs as a long-lived Deployment. |
| `serving/` | Kafka→Redis consumer and the read-only FastAPI service. |
| `dashboard/` | Next.js front end. Proxies the cluster API server-side to avoid mixed content. |
| `databricks/` | The original Databricks notebook and runbook. Not the live path — see below. Its `transforms.py` is still the single source of truth for alert thresholds. |
| `docs/superpowers/` | Design specs and implementation plans, one per sub-project. |

## Alert thresholds

Defined once, in `databricks/transforms.py`, and imported by the Spark job:

| Alert | Field | Fires above |
|---|---|---|
| `heat` | temperature | 40 °C |
| `heavy_rain` | rainfall | 50 mm |
| `high_wind` | wind_speed | 60 km/h |

The generator's extreme-value ranges start just above each threshold, so every
extreme reading it produces triggers exactly one alert.

## Why Spark runs in-cluster rather than on Databricks

The original design targeted Databricks Community Edition. That tier was retired
in favour of a serverless Free Edition which may not permit the
`spark.hadoop.fs.s3a.*` credential configuration the notebook depends on, and it
needs a browser signup that cannot be scripted.

Running the same logic on the cluster that already exists removed the account,
the manual setup, and the scheduled-run window in one move: the job is
continuous rather than batched every fifteen minutes, there is no image to build
or push (the job ships as a ConfigMap built from the real source files), and S3
credentials come from a Kubernetes Secret through S3A's environment provider
rather than being set into Spark config, where the Spark UI would expose them.

## Ports

| Port | Reached by | Open to |
|---|---|---|
| 22 | SSH | your IP only |
| 9094 | Kafka external listener | `0.0.0.0/0` |
| 30080 | Read-only weather API | `0.0.0.0/0` |

Kafka's external listener binds container port 9095 and is advertised as
`<elastic-ip>:9094`; the NodePort service bridges the two. Redis is
cluster-internal and authenticated, never exposed.
