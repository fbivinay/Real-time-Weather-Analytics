# Data Pipeline — Weather Generator + Kafka Producer (sub-project 2 of 5)

## Context

Part of the Real-Time Weather Analytics Pipeline. Sub-project 1 (infra)
stood up EC2 + k3s + Kafka (KRaft, internal DNS
`kafka.weather-pipeline.svc.cluster.local:9092`) + Redis. Infra
deliberately does not create application topics — this sub-project owns
and creates `weather-data`.

Full data flow (this sub-project is the first stage):

```
Weather Generator (this sub-project)
      │
      ▼
Kafka topic: weather-data
      │
      ▼
Databricks Structured Streaming        (sub-project 3)
      │
      ▼
Kafka topic: weather-processed
      │
      ▼
Redis → FastAPI → Next.js dashboard    (sub-project 4)
```

## Goals

- A container running on the k3s cluster continuously produces
  realistic weather readings for 5 stations to the `weather-data` Kafka
  topic.
- Readings are randomized within realistic ranges, with an occasional
  independent chance of an extreme value per field, so sub-project 3's
  alert logic has real data to trigger on without manual intervention.
- The producer owns `weather-data`'s existence — creates it on startup
  if missing (idempotent), rather than relying on Kafka's implicit
  auto-create.
- Deployable via a plain `kubectl apply` against the existing k3s
  cluster, image pulled from a public Docker Hub repo.

## Non-goals

- Multiple pods/replicas — single pod simulates all 5 stations
  internally (topology decision, see Design).
- Historical backfill or replay — this only produces live, ongoing data.
- Consuming or processing `weather-data` — that's sub-project 3.
- Any Databricks-side configuration.

## Design

**Topology:** one Python process, one container, `replicas: 1`. An
internal loop iterates the 5 stations every cycle and produces one
message per station per cycle — simpler to reason about and debug than
5 separate pods for a demo-scale project, and avoids needing per-pod
station configuration.

**Stations** (fixed list, hardcoded — no need for external config at
this scale):

| station_id | City |
|---|---|
| ST001 | Bengaluru |
| ST002 | Mysuru |
| ST003 | Chennai |
| ST004 | Hyderabad |
| ST005 | Mumbai |

**Cadence:** every 5 seconds, all 5 stations produce a reading in the
same cycle (5 messages every 5s, ~1 msg/sec average).

**Message schema** (JSON, one message per station per cycle):

```json
{
  "station_id": "ST001",
  "city": "Bengaluru",
  "timestamp": "2026-08-25T07:40:00Z",
  "temperature": 29.4,
  "humidity": 74,
  "rainfall": 2.4,
  "wind_speed": 18.7
}
```

- `timestamp`: UTC, ISO 8601, generated at send time.
- `temperature`: °C, float, 1 decimal.
- `humidity`: %, integer, 0-100.
- `rainfall`: mm, float, 1 decimal.
- `wind_speed`: km/h, float, 1 decimal.

**Normal ranges** (same across all stations — no per-city climate
modeling, out of scope for a demo):

| Field | Normal range |
|---|---|
| temperature | 15.0 – 35.0 °C |
| humidity | 40 – 90 % |
| rainfall | 0.0 – 10.0 mm |
| wind_speed | 5.0 – 25.0 km/h |

**Extreme value injection:** for each station on each cycle, there's an
independent ~1-in-30 chance (`random.random() < 1/30`, evaluated
separately, once per station per cycle) to replace one field with an
extreme value instead of a normal one. When triggered, one of three
extreme types is picked at random (equal weight):

| Type | Field | Extreme range |
|---|---|---|
| Heat | temperature | 40.1 – 45.0 °C |
| Heavy rain | rainfall | 50.1 – 100.0 mm |
| High wind | wind_speed | 60.1 – 90.0 km/h |

These thresholds match the alert conditions sub-project 3 is expected
to implement (`temperature > 40` → heat alert, high rainfall → rain
alert, high wind → wind alert), so this generator's extreme output is
exactly what sub-project 3's Structured Streaming alerts will need to
fire on. Only one field goes extreme per triggered station per cycle —
the other three fields stay in their normal ranges that cycle.

**Topic creation:** on startup, the producer creates `weather-data` via
`KafkaAdminClient.create_topics()` with `num_partitions=1`,
`replication_factor=1` (matches the single-broker cluster from
sub-project 1 — no benefit to more partitions/replicas here). Wrapped
to ignore `TopicAlreadyExistsError` so restarts and redeploys are
idempotent.

**Kafka client:** `kafka-python-ng` (pure Python, no native library
build step needed in the Dockerfile — keeps the image small and the
build simple; already proven working against this exact cluster during
sub-project 1's live verification).

**Bootstrap address:** `kafka.weather-pipeline.svc.cluster.local:9092`
— the cluster-internal DNS name and internal listener from
sub-project 1. This pod runs inside the same k3s cluster as Kafka, so
it uses the internal listener, not the external NodePort 9094 (that's
for Databricks, running outside the cluster).

## Files

- `producer/weather_generator.py` — station list, normal/extreme range
  logic, message construction. No Kafka dependency — pure data
  generation, testable standalone.
- `producer/kafka_producer.py` — topic creation, send loop, entrypoint
  (`if __name__ == "__main__"`).
- `producer/Dockerfile` — `python:3.12-slim` base, installs
  `kafka-python-ng`, copies the two `.py` files, runs
  `kafka_producer.py`.
- `producer/k8s-deployment.yaml` — a single `Deployment` manifest
  (`replicas: 1`), namespace `weather-pipeline` (matches sub-project
  1's namespace), image `fbivinay/weather-generator:latest`.
- `producer/requirements.txt` — `kafka-python-ng`.

## Deployment

1. `docker build -t fbivinay/weather-generator:latest producer/`
2. `docker push fbivinay/weather-generator:latest`
3. `kubectl apply -f producer/k8s-deployment.yaml`
4. Verify: `kubectl logs -n weather-pipeline -l app=weather-generator -f`
   should show periodic "produced N messages" log lines; a manual
   `kafka-console-consumer.sh --topic weather-data --from-beginning`
   (from inside the cluster, same pattern sub-project 1 used for its own
   verification) should show real JSON messages arriving continuously.

## Verification (definition of done for this sub-project)

1. Docker image builds and pushes successfully to Docker Hub.
2. `kubectl apply` succeeds, pod reaches `Running` state.
3. Pod logs show successful message production every ~5s, no crash
   loops.
4. `weather-data` topic exists (created by the producer, confirmed via
   `kafka-topics.sh --list`), with messages readable via a console
   consumer, matching the documented JSON schema.
5. Over a few minutes of observation, at least one extreme-value message
   appears (statistically likely given the 5 stations × 1-in-30 chance
   × ~12 cycles/minute — expected roughly one extreme reading every
   ~1-2 minutes across all stations combined), confirming the injection
   logic actually fires.

## Open follow-ups (tracked, not blocking)

- No per-station climate variation (all stations share the same normal
  ranges) — acceptable for a demo; could be revisited if realism
  becomes a requirement.
- No resource limits/requests set on the Deployment — fine at this
  scale, worth adding if the cluster gets busier once sub-projects 3-4
  are also running on it.
