# Data Pipeline: Weather Generator + Kafka Producer — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A containerized Python process that continuously generates
realistic weather readings for 5 stations and produces them to the
`weather-data` Kafka topic on the cluster built in sub-project 1, with
occasional extreme values to drive sub-project 3's alert logic.

**Architecture:** Two small, independently-testable Python modules
(pure data generation, and Kafka topic-creation/send-loop), packaged
into one Docker image, deployed as a single-replica Kubernetes
Deployment on the existing k3s cluster.

**Tech Stack:** Python 3.12, `kafka-python-ng` (pure-Python Kafka
client, no native build step), `pytest` for tests, Docker, Kubernetes
(k3s, from sub-project 1).

**Spec:** `docs/superpowers/specs/2026-08-25-data-pipeline-design.md`

## Global Constraints

- Kafka bootstrap address: `kafka.weather-pipeline.svc.cluster.local:9092`
  (internal cluster DNS/listener from sub-project 1 — never the
  external NodePort 9094, since this runs inside the cluster).
- Topic name: `weather-data`, created by this producer with
  `num_partitions=1`, `replication_factor=1`, idempotent (ignore
  already-exists).
- Cadence: every 5 seconds, all 5 stations produce one reading each.
- Extreme-value injection: independent ~1-in-30 chance
  (`random.random() < 1/30`) per station per cycle; when triggered,
  exactly one field (temperature, rainfall, or wind_speed, equal
  weight) is replaced with a value from its extreme range; the other
  fields stay in their normal ranges that cycle.
- Normal ranges: temperature 15.0–35.0°C, humidity 40–90%, rainfall
  0.0–10.0mm, wind_speed 5.0–25.0km/h.
- Extreme ranges: temperature 40.1–45.0°C, rainfall 50.1–100.0mm,
  wind_speed 60.1–90.0km/h.
- Stations (fixed, hardcoded): ST001/Bengaluru, ST002/Mysuru,
  ST003/Chennai, ST004/Hyderabad, ST005/Mumbai.
- Namespace: `weather-pipeline` (matches sub-project 1's Kafka/Redis
  namespace).
- Docker image: `fbivinay/weather-generator:latest`.
- Topology: single pod, `replicas: 1`, no per-station pods.

---

## Task 1: `weather_generator.py` — pure data generation logic

**Files:**
- Create: `producer/weather_generator.py`
- Create: `producer/tests/test_weather_generator.py`
- Create: `producer/requirements-dev.txt`

**Interfaces:**
- Produces: `STATIONS` (list of 5 dicts, each `{"station_id": str,
  "city": str}`), `generate_reading(station: dict, rand=random) -> dict`,
  `generate_all_readings(rand=random) -> list[dict]` — consumed by
  Task 2's `kafka_producer.py`.
- Each reading dict has exactly the keys: `station_id`, `city`,
  `timestamp`, `temperature`, `humidity`, `rainfall`, `wind_speed`.

- [x] **Step 1: Write `producer/requirements-dev.txt`**

```
pytest==8.3.4
```

- [x] **Step 2: Write the failing tests**

Create `producer/tests/test_weather_generator.py`:

```python
from unittest.mock import MagicMock
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import weather_generator as wg


def test_stations_list_has_five_entries_with_correct_ids():
    ids = [s["station_id"] for s in wg.STATIONS]
    assert ids == ["ST001", "ST002", "ST003", "ST004", "ST005"]


def test_generate_reading_normal_no_extreme():
    fake_rand = MagicMock()
    fake_rand.uniform.side_effect = [20.0, 5.0, 15.0]
    fake_rand.randint.return_value = 60
    fake_rand.random.return_value = 0.5  # >= 1/30, no extreme trigger

    station = {"station_id": "ST001", "city": "Bengaluru"}
    reading = wg.generate_reading(station, rand=fake_rand)

    assert reading["station_id"] == "ST001"
    assert reading["city"] == "Bengaluru"
    assert reading["temperature"] == 20.0
    assert reading["humidity"] == 60
    assert reading["rainfall"] == 5.0
    assert reading["wind_speed"] == 15.0
    assert "timestamp" in reading


def test_generate_reading_extreme_triggers_temperature():
    fake_rand = MagicMock()
    fake_rand.uniform.side_effect = [20.0, 5.0, 15.0, 42.0]
    fake_rand.randint.return_value = 60
    fake_rand.random.return_value = 0.0  # < 1/30, triggers extreme
    fake_rand.choice.return_value = "temperature"

    station = {"station_id": "ST001", "city": "Bengaluru"}
    reading = wg.generate_reading(station, rand=fake_rand)

    assert reading["temperature"] == 42.0
    assert reading["rainfall"] == 5.0
    assert reading["wind_speed"] == 15.0


def test_generate_reading_extreme_triggers_rainfall():
    fake_rand = MagicMock()
    fake_rand.uniform.side_effect = [20.0, 5.0, 15.0, 75.0]
    fake_rand.randint.return_value = 60
    fake_rand.random.return_value = 0.0
    fake_rand.choice.return_value = "rainfall"

    station = {"station_id": "ST002", "city": "Mysuru"}
    reading = wg.generate_reading(station, rand=fake_rand)

    assert reading["temperature"] == 20.0
    assert reading["rainfall"] == 75.0
    assert reading["wind_speed"] == 15.0


def test_generate_reading_schema_has_exact_keys():
    fake_rand = MagicMock()
    fake_rand.uniform.side_effect = [20.0, 5.0, 15.0]
    fake_rand.randint.return_value = 60
    fake_rand.random.return_value = 0.5

    station = {"station_id": "ST001", "city": "Bengaluru"}
    reading = wg.generate_reading(station, rand=fake_rand)

    assert set(reading.keys()) == {
        "station_id", "city", "timestamp",
        "temperature", "humidity", "rainfall", "wind_speed",
    }


def test_generate_all_readings_returns_five_in_station_order():
    readings = wg.generate_all_readings()
    assert len(readings) == 5
    assert [r["station_id"] for r in readings] == \
        ["ST001", "ST002", "ST003", "ST004", "ST005"]
```

- [x] **Step 3: Run tests to verify they fail**

Run: `cd producer && pip install -r requirements-dev.txt && python -m pytest tests/test_weather_generator.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'weather_generator'`

- [x] **Step 4: Write `producer/weather_generator.py`**

```python
import random
from datetime import datetime, timezone

STATIONS = [
    {"station_id": "ST001", "city": "Bengaluru"},
    {"station_id": "ST002", "city": "Mysuru"},
    {"station_id": "ST003", "city": "Chennai"},
    {"station_id": "ST004", "city": "Hyderabad"},
    {"station_id": "ST005", "city": "Mumbai"},
]

NORMAL_RANGES = {
    "temperature": (15.0, 35.0),
    "humidity": (40, 90),
    "rainfall": (0.0, 10.0),
    "wind_speed": (5.0, 25.0),
}

EXTREME_RANGES = {
    "temperature": (40.1, 45.0),
    "rainfall": (50.1, 100.0),
    "wind_speed": (60.1, 90.0),
}

EXTREME_CHANCE = 1 / 30


def generate_reading(station, rand=random):
    temperature = round(rand.uniform(*NORMAL_RANGES["temperature"]), 1)
    humidity = rand.randint(*NORMAL_RANGES["humidity"])
    rainfall = round(rand.uniform(*NORMAL_RANGES["rainfall"]), 1)
    wind_speed = round(rand.uniform(*NORMAL_RANGES["wind_speed"]), 1)

    if rand.random() < EXTREME_CHANCE:
        extreme_field = rand.choice(list(EXTREME_RANGES.keys()))
        extreme_value = round(rand.uniform(*EXTREME_RANGES[extreme_field]), 1)
        if extreme_field == "temperature":
            temperature = extreme_value
        elif extreme_field == "rainfall":
            rainfall = extreme_value
        elif extreme_field == "wind_speed":
            wind_speed = extreme_value

    return {
        "station_id": station["station_id"],
        "city": station["city"],
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "temperature": temperature,
        "humidity": humidity,
        "rainfall": rainfall,
        "wind_speed": wind_speed,
    }


def generate_all_readings(rand=random):
    return [generate_reading(station, rand=rand) for station in STATIONS]
```

- [x] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_weather_generator.py -v`
Expected: PASS, 6/6 tests passing

- [x] **Step 6: Commit**

```bash
git add producer/weather_generator.py producer/tests/test_weather_generator.py producer/requirements-dev.txt
git commit -m "data-pipeline: weather generator pure logic (stations, normal/extreme ranges)"
```

---

## Task 2: `kafka_producer.py` — topic creation + send loop

**Files:**
- Create: `producer/kafka_producer.py`
- Create: `producer/tests/test_kafka_producer.py`
- Create: `producer/requirements.txt`

**Interfaces:**
- Consumes: `weather_generator.generate_all_readings()` (Task 1).
- Produces: `TOPIC_NAME = "weather-data"`,
  `BOOTSTRAP_SERVERS = "kafka.weather-pipeline.svc.cluster.local:9092"`,
  `ensure_topic_exists(bootstrap_servers=BOOTSTRAP_SERVERS, topic_name=TOPIC_NAME)`,
  `build_producer(bootstrap_servers=BOOTSTRAP_SERVERS) -> KafkaProducer`,
  `run(producer=None, interval_seconds=PRODUCE_INTERVAL_SECONDS,
  iterations=None)` — `iterations=None` runs forever (production use);
  a finite `iterations` value is what Task 3's Docker smoke test and
  this task's own tests use to run a bounded number of cycles.
  Consumed by Task 3 (Dockerfile's `CMD`) and Task 5 (live deployment).

- [x] **Step 1: Write `producer/requirements.txt`**

```
kafka-python-ng==2.2.3
```

- [x] **Step 2: Write the failing tests**

Create `producer/tests/test_kafka_producer.py`:

```python
from unittest.mock import MagicMock, patch
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import kafka_producer as kp


def test_run_produces_five_messages_per_iteration():
    fake_producer = MagicMock()
    kp.run(producer=fake_producer, interval_seconds=0, iterations=1)
    assert fake_producer.send.call_count == 5
    fake_producer.flush.assert_called_once()


def test_run_sends_to_correct_topic():
    fake_producer = MagicMock()
    kp.run(producer=fake_producer, interval_seconds=0, iterations=1)
    for call in fake_producer.send.call_args_list:
        assert call.args[0] == kp.TOPIC_NAME


def test_run_multiple_iterations():
    fake_producer = MagicMock()
    kp.run(producer=fake_producer, interval_seconds=0, iterations=3)
    assert fake_producer.send.call_count == 15
    assert fake_producer.flush.call_count == 3


def test_ensure_topic_exists_creates_topic():
    fake_admin = MagicMock()
    with patch("kafka_producer.KafkaAdminClient", return_value=fake_admin):
        kp.ensure_topic_exists()
    fake_admin.create_topics.assert_called_once()
    fake_admin.close.assert_called_once()


def test_ensure_topic_exists_ignores_already_exists_error():
    fake_admin = MagicMock()
    fake_admin.create_topics.side_effect = kp.TopicAlreadyExistsError("already exists")
    with patch("kafka_producer.KafkaAdminClient", return_value=fake_admin):
        kp.ensure_topic_exists()  # must not raise
    fake_admin.close.assert_called_once()
```

- [x] **Step 3: Run tests to verify they fail**

Run: `python -m pytest tests/test_kafka_producer.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'kafka_producer'`

- [x] **Step 4: Write `producer/kafka_producer.py`**

```python
import json
import logging
import time

from kafka import KafkaProducer, KafkaAdminClient
from kafka.admin import NewTopic
from kafka.errors import TopicAlreadyExistsError

import weather_generator

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("kafka_producer")

BOOTSTRAP_SERVERS = "kafka.weather-pipeline.svc.cluster.local:9092"
TOPIC_NAME = "weather-data"
PRODUCE_INTERVAL_SECONDS = 5


def ensure_topic_exists(bootstrap_servers=BOOTSTRAP_SERVERS, topic_name=TOPIC_NAME):
    admin = KafkaAdminClient(bootstrap_servers=bootstrap_servers)
    try:
        admin.create_topics([NewTopic(name=topic_name, num_partitions=1, replication_factor=1)])
        logger.info("Created topic %s", topic_name)
    except TopicAlreadyExistsError:
        logger.info("Topic %s already exists, reusing it", topic_name)
    finally:
        admin.close()


def build_producer(bootstrap_servers=BOOTSTRAP_SERVERS):
    return KafkaProducer(
        bootstrap_servers=bootstrap_servers,
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
    )


def run(producer=None, interval_seconds=PRODUCE_INTERVAL_SECONDS, iterations=None):
    if producer is None:
        producer = build_producer()

    count = 0
    while iterations is None or count < iterations:
        readings = weather_generator.generate_all_readings()
        for reading in readings:
            producer.send(TOPIC_NAME, value=reading)
        producer.flush()
        logger.info("Produced %d messages", len(readings))
        count += 1
        if iterations is None or count < iterations:
            time.sleep(interval_seconds)


if __name__ == "__main__":
    ensure_topic_exists()
    run()
```

- [x] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_kafka_producer.py -v`
Expected: PASS, 5/5 tests passing

- [x] **Step 6: Run the full test suite together**

Run: `python -m pytest tests/ -v`
Expected: PASS, 11/11 tests passing, pristine output (no warnings)

- [x] **Step 7: Commit**

```bash
git add producer/kafka_producer.py producer/tests/test_kafka_producer.py producer/requirements.txt
git commit -m "data-pipeline: Kafka producer (idempotent topic creation, send loop)"
```

---

## Task 3: Dockerfile — containerize the producer

**Files:**
- Create: `producer/Dockerfile`
- Create: `producer/.dockerignore`

**Interfaces:**
- Consumes: `producer/requirements.txt`, `producer/weather_generator.py`,
  `producer/kafka_producer.py` (Tasks 1-2).
- Produces: a built image importable/runnable as
  `fbivinay/weather-generator:latest` — consumed by Task 4's K8s
  manifest and Task 5's live deployment.

- [x] **Step 1: Write `producer/.dockerignore`**

```
tests/
requirements-dev.txt
__pycache__/
*.pyc
.pytest_cache/
```

- [x] **Step 2: Write `producer/Dockerfile`**

```dockerfile
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY weather_generator.py kafka_producer.py .

CMD ["python", "kafka_producer.py"]
```

- [x] **Step 3: Build the image**

Run: `cd producer && docker build -t fbivinay/weather-generator:latest .`
Expected: build succeeds, no errors.

If `docker` is not installed/available in the environment, this step
and the smoke test below are blocked — report BLOCKED with that
specific detail rather than skipping the build, since Task 5 depends
on a working image.

- [x] **Step 4: Smoke-test the image (import check, no live Kafka needed)**

Run:
```bash
docker run --rm --entrypoint python fbivinay/weather-generator:latest \
  -c "import kafka_producer; import weather_generator; print('OK')"
```
Expected: prints `OK`, exit code 0. This confirms the image has correct
dependencies and no import/syntax errors, without needing a live Kafka
connection (the container's default `CMD` would otherwise hang trying
to connect to Kafka at startup).

- [x] **Step 5: Commit**

```bash
git add producer/Dockerfile producer/.dockerignore
git commit -m "data-pipeline: Dockerfile for weather generator"
```

---

## Task 4: Kubernetes Deployment manifest

**Files:**
- Create: `producer/k8s-deployment.yaml`

**Interfaces:**
- Consumes: image tag `fbivinay/weather-generator:latest` (Task 3),
  namespace `weather-pipeline` (from sub-project 1's infra).
- Produces: a manifest consumed by Task 5's `kubectl apply`.

- [x] **Step 1: Write `producer/k8s-deployment.yaml`**

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: weather-generator
  namespace: weather-pipeline
  labels:
    app: weather-generator
spec:
  replicas: 1
  selector:
    matchLabels:
      app: weather-generator
  template:
    metadata:
      labels:
        app: weather-generator
    spec:
      containers:
        - name: weather-generator
          image: fbivinay/weather-generator:latest
          imagePullPolicy: Always
```

- [x] **Step 2: Validate as YAML**

Run: `python -c "import yaml; yaml.safe_load(open('producer/k8s-deployment.yaml')); print('valid')"`
Expected: `valid`

(Full schema/cluster validation happens in Task 5 against the real
k3s API server — this step only catches YAML syntax errors early,
without requiring cluster connectivity.)

- [x] **Step 3: Commit**

```bash
git add producer/k8s-deployment.yaml
git commit -m "data-pipeline: Kubernetes Deployment manifest for weather generator"
```

---

## Task 5: Build, push, deploy, and live-verify against the real cluster

No new application code — this task is the integration test: push the
real image, deploy it to the real k3s cluster (from sub-project 1), and
confirm messages actually flow into `weather-data`. Do this task
deliberately, not as a background step: it needs a live k3s cluster,
which means either sub-project 1's EC2 instance is already running, or
it needs to be brought up first (`terraform apply` in `infra/`,
real AWS cost while running — see sub-project 1's own README for the
apply/bootstrap sequence if starting from nothing).

Unlike sub-project 1's final task, this task does **not** end with
`terraform destroy` — sub-project 3 (Databricks) needs the same running
cluster next. Leave the instance up after this task's verification
passes; destroying it (when the whole multi-sub-project session is
done for the day) is a separate decision for whoever is driving at that
point.

**Files:** none created; this task only runs commands.

**Interfaces:**
- Consumes: `fbivinay/weather-generator:latest` image (Task 3),
  `producer/k8s-deployment.yaml` (Task 4), the live k3s cluster and
  `weather-pipeline` namespace (sub-project 1).

- [x] **Step 1: Confirm the cluster is reachable, or bring it up**

Run: `kubectl get nodes` (using the kubeconfig from sub-project 1's
runbook — either the SSH-tunneled config, or run this over SSH on the
instance itself).
Expected: one node, `Ready`.

If no cluster is reachable, follow `infra/README.md`'s runbook
(`terraform apply` → wait for user-data → confirm `kubectl get nodes`)
before continuing. This incurs real AWS cost while the instance runs.

- [x] **Step 2: Log in to Docker Hub and push the image**

Run: `docker login` (interactive, one-time), then:
```bash
cd producer && docker push fbivinay/weather-generator:latest
```
Expected: push succeeds, image visible at
`https://hub.docker.com/r/fbivinay/weather-generator`.

- [x] **Step 3: Deploy to the cluster**

Run: `kubectl apply -f producer/k8s-deployment.yaml`
Expected: `deployment.apps/weather-generator created` (or `configured`
if re-running).

- [x] **Step 4: Confirm the pod reaches Running**

Run: `kubectl get pods -n weather-pipeline -l app=weather-generator -w`
(watch until `Running`, then Ctrl-C)
Expected: pod status `Running`, no `CrashLoopBackOff`.

- [x] **Step 5: Confirm logs show successful production**

Run: `kubectl logs -n weather-pipeline -l app=weather-generator --tail=20`
Expected: log lines like `Produced 5 messages`, repeating roughly every
5 seconds, no tracebacks.

- [x] **Step 6: Confirm messages are actually in the topic with the right shape**

Run (from inside the cluster, same pattern sub-project 1 used for its
own Kafka verification — exec into the running Kafka broker pod):
```bash
kubectl exec kafka-controller-0 -n weather-pipeline -- bash -c \
  "kafka-console-consumer.sh --bootstrap-server kafka.weather-pipeline.svc.cluster.local:9092 --topic weather-data --from-beginning --max-messages 5"
```
Expected: 5 JSON lines, each with exactly the keys `station_id`,
`city`, `timestamp`, `temperature`, `humidity`, `rainfall`,
`wind_speed`.

- [x] **Step 7: Observe for at least one extreme reading**

Run (let it run a couple minutes, watching logs or re-running the
consumer command above with a higher `--max-messages`):
```bash
kubectl exec kafka-controller-0 -n weather-pipeline -- bash -c \
  "kafka-console-consumer.sh --bootstrap-server kafka.weather-pipeline.svc.cluster.local:9092 --topic weather-data --from-beginning --max-messages 60"
```
Expected: scanning the output, at least one message has
`temperature > 40`, `rainfall > 50`, or `wind_speed > 60` — confirms
the extreme-injection logic (Task 1) actually fires in the live
container, not just in unit tests.

This step satisfies the spec's Verification item 5.

- [x] **Step 8: No commit** — this task is verification-only; nothing
  new to commit unless Steps 1-7 revealed a bug requiring a code fix,
  in which case fix it, re-run the relevant steps, and commit the fix
  with a message describing what broke and why.

**Verified 2026-08-25 16:52 IST:** re-confirmed live via SSH tunnel to
sub-project 1's cluster. Pod `weather-generator-66fdcb5bc8-zp4kk`
Running 51m, logs show steady `Produced 5 messages`. Dumped full
`weather-data` topic (3655 msgs, from-beginning) and grepped for
extreme thresholds — found multiple: heat (44.3°C, 44.7°C), heavy
rain (72.0mm, 83.8mm), high wind (68.1, 87.7 km/h). Message shape
matches spec exactly (7 keys, correct types).
