# WeatherOps — Real-Time Weather Risk & Operations Intelligence

Design spec. Supersedes the threshold-alert pipeline described in the
2026-08-25 specs while reusing its infrastructure.

## Context

The Real-Time Weather Analytics pipeline works end to end: five simulated
stations → Kafka → in-cluster Spark Structured Streaming → Kafka → consumer →
Redis → FastAPI → Next.js on Vercel, with Parquet history on S3, all on one
k3s node in AWS (Terraform). It answers "what is the temperature" but not
"what should operations do about it".

WeatherOps reframes it for **logistics and delivery operations in India**:
turn real-time weather into operational decisions. For every place the
network touches it answers: what weather risk is developing, where, which
routes, hubs and deliveries it affects, and what an operator should consider
doing.

Priorities, in order: real-time streaming → risk detection → prediction →
operational impact → actionable dashboard.

## Goals

- Real weather (Open-Meteo) for 40 Indian cities plus ~110 simulated
  company hub sensors, with a scenario simulator and historical replay.
- Spark does validation, quarantine, deduplication, late-data handling and
  windowed aggregation.
- A risk engine computes a 0–100 risk score with Low/Medium/High/Critical
  categories from rain, wind, heat, visibility, forecast and historical
  conditions; separates sensor faults from genuine weather; maps risk onto
  routes, hubs and deliveries; opens and resolves incidents with hysteresis;
  recommends rule-based actions.
- A LightGBM model forecasts conditions 60 minutes ahead, evaluated honestly
  against a persistence baseline.
- A FastAPI service pushes changes over WebSockets to a Next.js dashboard
  with a MapLibre map of India.
- Data-quality and pipeline-health signals are visible in the dashboard.
- The cluster runs on demand; when it is down the dashboard plays a recording.

## Non-goals

- No LLM or chatbot. Actions come from a rule table.
- No deep learning. No online retraining.
- No PostgreSQL: Redis holds live state, S3 holds history. Add Postgres only
  if an acknowledge/assign workflow or audit queries are needed.
- No Prometheus/Grafana: pipeline-native metrics → Redis → dashboard panel.
- No operator write actions (acknowledge, assign). The API stays read-only
  and unauthenticated; recommendations are not sent anywhere.
- No new services. Workloads stay five: ingestor, Spark processor, risk
  engine, API, dashboard. cert-manager is a cluster add-on.
- Road-accurate routing. Corridors are straight segments between real
  waypoint cities; the README says so.

## Decisions (from brainstorming, 2026-10-01)

| Topic | Decision |
|---|---|
| Where decisions run | Python risk engine after Spark (evolved `consumer.py`), not inside Spark |
| Runtime | On-demand cluster; Elastic IP, S3, IAM persist; dashboard plays a recording when the cluster is down |
| WebSocket transport | TLS in-cluster: Traefik (bundled with k3s) + cert-manager + Let's Encrypt for a free DuckDNS name; Vercel proxy deleted |
| Real data | Open-Meteo forecast API (live) and Historical Forecast API (replay, training), CC BY 4.0, non-commercial |
| Sensors | Company hub sensors are simulated in every mode and labelled as such |
| Model | LightGBM, +60 min horizon, city level |

## Architecture

```
Open-Meteo (live + historical)
        │
ingestor ─ mode: live | replay <event> | sim <scenario>
  40 city reference readings + ~110 simulated hub sensors + fault injection
        ▼
Kafka weather-data
        ▼
Spark ─┬─ raw ──────────────────────────────────▶ S3 raw/
       ├─ invalid ─▶ Kafka weather-quarantine ──▶ S3 quarantine/
       └─ valid → dedup(station_id, seq) → 30 s windows/station
                 ─▶ Kafka weather-features ─────▶ S3 features/
        ▼
risk engine (serving/engine.py), tick every 10 s:
  sensor verdicts → risk 0–100 → +60 min forecast → route/hub/delivery impact
  → incidents + actions
        ├─▶ Redis: live state + pub/sub channel weatherops:events
        └─▶ Kafka weather-decisions ─▶ (Spark) S3 decisions/
        ▼
api (FastAPI): REST snapshot + WebSocket /ws
  behind Traefik + Let's Encrypt at <name>.duckdns.org
        ▼
dashboard (Vercel): MapLibre India map, incidents + actions, data quality,
  health; plays a recording when the cluster is unreachable
```

End-to-end latency from reading to dashboard is about 45–60 s (30 s window,
15 s allowed lateness, 5 s triggers, 10 s engine tick). That is adequate for
dispatch decisions and is stated in the README.

## Contracts

### Two clocks

- `event_time` — pipeline time. When the ingestor emitted the reading. Drives
  Spark watermarks, lateness and latency metrics.
- `observed_at` — weather time. When the conditions were valid. Drives rain
  accumulation, ML lags, climatology and delivery time-of-day.

In live mode they differ only for reference readings (`observed_at` is
Open-Meteo's 15-minute interval time). In replay `observed_at` is compressed
(default 120×) while `event_time` stays wall clock, so Spark behaves
identically in every mode. Live backfill uses the same mechanism.

Rule for every consumer of these clocks: **weather quantities use
`observed_at`; sensor behaviour and pipeline health use window counts or
`event_time`.** Faults and noise happen to physical sensors in real time, so
anomaly statistics are computed over the last N windows, not N observed
minutes. At 120× one 30-second window spans an observed hour, so anything
that looks back in observed time must accept "at least the latest window"
and interpolate between window midpoints rather than expect a sample at an
exact offset.

### Reading v2 (`weather-data`, JSON)

| Field | Type | Notes |
|---|---|---|
| `station_id` | string | `REF-<CITY>` for references, `<HUB>-S<n>` for sensors |
| `kind` | string | `reference` \| `sensor` |
| `source` | string | `live` \| `replay` \| `sim` |
| `scenario` | string \| null | replay event or sim scenario id |
| `seq` | int | per-station counter since ingestor start |
| `event_time` | ISO-8601 UTC | pipeline time |
| `observed_at` | ISO-8601 UTC | weather time |
| `lat`, `lon` | float | station position |
| `temperature_c` | float | |
| `humidity_pct` | float | |
| `rain_mmph` | float | intensity; Open-Meteo 15-min precipitation × 4 |
| `wind_kmph` | float | sustained, 10 m |
| `gust_kmph` | float | |
| `visibility_m` | float | |
| `pressure_hpa` | float | mean sea level |
| `rain_24h_mm` | float \| null | references only: antecedent rain |

Any measurement may be null (missing value). Valid ranges live in
`weatherops/schema.py` (`RANGES`), shared by ingestor tests and Spark:

| Field | Min | Max |
|---|---|---|
| temperature_c | -40 | 60 |
| humidity_pct | 0 | 100 |
| rain_mmph | 0 | 500 |
| wind_kmph | 0 | 300 |
| gust_kmph | 0 | 400 |
| visibility_m | 0 | 100000 |
| pressure_hpa | 850 | 1100 |
| rain_24h_mm | 0 | 2000 |

Reject reasons, first match wins: `unparseable` (no `station_id` or
`event_time`), `<field>_out_of_range`, `future_timestamp` (`event_time` more
than 2 minutes ahead of processing time).

### Topics

Created idempotently by `deploy.sh` (`kafka-topics.sh --create
--if-not-exists`), one partition, 24 h retention. No application creates
topics. `weather-processed` is retired.

| Topic | Producer | Consumers |
|---|---|---|
| `weather-data` | ingestor | Spark |
| `weather-quarantine` | Spark | engine |
| `weather-features` | Spark | engine |
| `weather-decisions` | engine | Spark (S3 audit) |

### Feature window (`weather-features`, JSON)

One record per station per closed 30-second event-time window:

```
station_id, kind, source, scenario,
window_start, window_end, observed_from, observed_to,
readings, seq_min, seq_max, delayed, max_delay_s,
temp_min, temp_avg, temp_max, humidity_avg,
rain_avg, rain_max, wind_avg, gust_max,
visibility_min, pressure_avg, rain_24h
```

`delayed` counts readings whose Kafka timestamp is more than 10 s after
their `event_time` (out of order but within the watermark).

### Quarantine record (`weather-quarantine`, JSON)

`reason, station_id, event_time, kafka_ts, raw` (raw is the original message
string, truncated to 1 KB).

### Decision records (`weather-decisions`, JSON)

- `{"record_type": "incident", ...incident}` on every incident change.
- `{"record_type": "snapshot", "observed_at", "kpis", "locations": [...]}`
  once per observed minute.

### Redis keys (engine writes, API reads)

| Key | Type | Content |
|---|---|---|
| `state:mode` | string | `{source, scenario, speed, observed_at}` |
| `state:locations` | hash | station_id → location state |
| `state:routes` | hash | route_id → route state |
| `state:hubs` | hash | hub_id → hub state |
| `state:kpis` | string | KPI object |
| `incidents:active` | hash | incident_id → incident |
| `incidents:history` | sorted set | resolved incidents, score = resolved epoch, trimmed to 7 days |
| `dq:summary` | string | quarantine by reason, drops, missing, suspect sensors |
| `health:engine` | string | tick time, consumer lag, latency p50/p95, freshness |
| `health:spark:<query>` | string, TTL 300 s | latest StreamingQueryProgress digest |
| `weatherops:events` | pub/sub channel | `tick`, `incident`, `mode` messages |

### API

| Endpoint | Returns |
|---|---|
| `GET /api/health` | per-component status `ok`/`degraded`/`down` with metrics |
| `GET /api/snapshot` | mode, KPIs, locations, routes, hubs, active incidents, DQ, health |
| `GET /api/incidents?status=active\|history&limit=` | incidents |
| `GET /api/stations/{id}` | last 60 observed minutes of windows, factor breakdown, forecast |
| `GET /api/model` | model card |
| `WS /ws` | `snapshot` on connect, then `tick` / `incident` / `mode` messages, ping every 20 s |

Read-only, no credentials, CORS `*` for GET. Today's `/api/stations`,
`/api/alerts`, `/api/stats` are removed with the old dashboard.

## Components

### 1. `weatherops/` — shared pure-Python package

No I/O, no Kafka or Redis imports. Deployed by ConfigMap alongside each
service that uses it. Replaces `databricks/transforms.py` as the single
source of truth.

| Module | Responsibility |
|---|---|
| `geo.py` | haversine, destination point, polyline sampling |
| `schema.py` | `RANGES`, `validate(reading) -> reason \| None` |
| `network.py` | cities, hubs, sensors, linehaul corridors, last-mile routes, GeoJSON export |
| `risk.py` | factor anchors, heat index, `score(conditions, climatology, forecast)` |
| `anomaly.py` | sensor verdicts |
| `impact.py` | route/hub exposure, delivery curves, KPIs, reroute search |
| `incidents.py` | incident lifecycle with hysteresis |
| `actions.py` | rule table → recommended actions |
| `forecast.py` | per-city feature builder, model wrapper |
| `climatology.json` | per city × month percentiles (generated by `ml/`) |

**Network.** 40 reference cities covering every region. 25 hubs at real
logistics clusters (Bhiwandi for Mumbai, Hoskote for Bengaluru,
Sriperumbudur for Chennai, Dankuni for Kolkata, …), each tied to its city.
4–5 sensors per hub, 2–12 km from it, by seeded bearings (~110 total).
About 30 linehaul corridors as hub-to-hub edges with optional waypoint
cities for shape; they form a graph used for reroute search. Eight last-mile
spokes per hub, 6–15 km. Route sample points every 5 km (linehaul) and 1 km
(last-mile). All generated deterministically from a fixed seed.

### 2. Ingestor (`ingestor/`, was `producer/`)

One loop. Every 10 s (`SENSOR_INTERVAL_S`) it asks the active source for
city conditions at the current `observed_at`, emits reference readings when
they change, derives sensor readings, applies faults, sends to Kafka. A
1-second inner tick releases delayed messages.

Sources:

- `live` — Open-Meteo `current` for all 40 cities in one HTTP request every
  15 minutes (stdlib `urllib`). Seven variables (≤10, so no fractional call
  weighting) plus hourly precipitation for `rain_24h_mm`. 40 locations × 96
  polls ≈ 3,840 calls/day against a 10,000/day free limit. On start it
  backfills the last 3 hours by replaying them at 120× (90 seconds), so each
  backfilled hour lands in its own Spark window instead of being averaged
  into one. On error or HTTP 429 it keeps the last values and backs off;
  reference staleness then appears in the health panel.
- `replay <event>` — Historical Forecast API, all 40 cities, hourly, fetched
  once at start, interpolated linearly in `observed_at`. Default speed 120×.
  Playback starts 3 hours before the event window (a 90-second pre-roll that
  fills forecast lags), runs the window, then loops. Event catalogue in
  `ingestor/events.py`; an event stays only if its data actually shows the
  hazard. Events used to demo forecasts must fall in the held-out 2025 test
  year.
- `sim <scenario>` — seeded synthetic weather: diurnal baseline per city,
  moving storm cells (track, radius, peak rain and gust, grow–peak–decay),
  fog banks, heat domes, evaluated at each station's own coordinates.
  Scenarios in `ingestor/scenarios.py` give tests known ground truth.

Hub sensors follow their city reference through a first-order lag (so
15-minute reference steps don't show as jumps) plus per-sensor noise.
Occasional hub-local micro-events (rain burst, gusts, rain-cooled air)
affect all sensors at one hub and not the reference — genuine local weather
the anomaly detector must classify as `weather_event`.

Faults (sensors only; seeded; `FAULT_RATE` multiplier, default ≈0.25 fault
episodes per sensor-hour): spike, stuck, drift, dropout, invalid value,
duplicate send, delayed send (20–120 s). Ground truth is logged, never sent.

Configuration by environment: `MODE`, `SCENARIO`, `REPLAY_SPEED`,
`SENSOR_INTERVAL_S`, `POLL_MINUTES`, `FAULT_RATE`, `SEED`,
`KAFKA_BOOTSTRAP`.

Deployment: ConfigMap from source (`ingestor/` + `weatherops/`) on
`python:3.12-slim`, `pip install kafka-python-ng` at start. Dockerfile and
the Docker Hub image are deleted.

### 3. Spark processor (`spark_processor/`)

Kept: Kafka source, `write_kafka`/`write_s3` helpers, S3A credentials from
environment, checkpoint PVC, `Recreate` strategy, ConfigMap from source.
Removed: threshold-alert branch, `transforms.py` import, `databricks/`.

`plans.py` holds pure DataFrame → DataFrame functions so the same code runs
streaming in production and batch in tests:

1. `parse` — `from_json` with the v2 schema; keeps raw string and Kafka
   timestamp.
2. `validate` — adds `reject_reason` built from `weatherops.schema.RANGES`
   with the same rule order as `schema.validate()`.
3. `features` — valid rows → `withWatermark(event_time, 15 s)` →
   `dropDuplicatesWithinWatermark(station_id, seq)` → 30-second tumbling
   window per station → feature record, append mode.

Queries:

| Query | Sink | Trigger |
|---|---|---|
| raw | S3 `raw/` | 60 s |
| quarantine | Kafka `weather-quarantine` | 5 s |
| quarantine | S3 `quarantine/` | 60 s |
| features | Kafka `weather-features` | 5 s |
| features | S3 `features/` | 60 s |
| decisions | `weather-decisions` → S3 `decisions/` | 60 s |

A Python `StreamingQueryListener` writes each query's progress digest to
`health:spark:<query>`: input and processed rows/s, batch duration,
watermark, Kafka offsets behind latest, rows dropped by watermark,
duplicates dropped. `redis` is pip-installed into a writable target at
container start.

Risk: chaining dedup and window aggregation in append mode relies on
multiple stateful operators (Spark ≥3.4). The first Spark task proves it.
Fallback: drop the dedup operator and count duplicates per window as
`count(*) − size(collect_set(seq))`.

### 4. Risk engine (`serving/engine.py`, was `consumer.py`)

Consumes `weather-features` and `weather-quarantine` (group `risk-engine`).
Keeps, per station, the windows covering the last 4 observed hours and at
least the last 20 windows. Ticks every 10 s. Resets all state when
`(source, scenario)` changes or `observed_at` regresses by more than an
hour.

**Sensor verdicts** (`anomaly.py`), per sensor per tick, robust z-scores
(median, 1.4826·MAD) over the sensor's last 20 windows (10 minutes of
pipeline time — faults happen in real time, whatever the replay speed):

- `stale` — no window for more than 2 window lengths of `event_time`.
- `sensor_suspect` with reason:
  - `spike` — residual against the median of the other sensors at the hub
    has |z| > 6 while those sensors hold steady.
  - `stuck` — temperature, humidity and pressure identical for 6 or more
    consecutive windows (healthy sensors always carry noise).
  - `drift` — median residual over 20 windows beyond a bound (temperature
    2.5 °C, pressure 4 hPa) and growing.
- `weather_event` — the sensor departs from its own history (|z| > 4) and
  at least half of the other sensors at the hub move the same way.
- `ok` otherwise.

Suspect and stale sensors are excluded from risk. A hub's conditions are the
median of its trusted sensors, falling back to the city reference.

**Risk score** (`risk.py`). Sub-scores in [0, 1], linear between anchors:

| Factor | Input | 0 | 0.3 | 0.5 | 0.75–0.8 | 1.0 | Anchor basis |
|---|---|---|---|---|---|---|---|
| Rain intensity | mm/h, mean over windows in the last 15 observed min (at least the latest) | ≤2.5 | 7.5 | 15 | 30 | ≥50 | heavy rain ≥7.6 mm/h |
| Rain accumulation | mm, 3 h + 0.25 × 24 h (Σ window rain_avg × observed duration) | ≤20 | — | ~70 | 100 | ≥150 | IMD heavy 64.5 mm/day |
| Gust | km/h, max over the same 15-min set | ≤40 | 50 (0.25) | 62 | 89 | ≥118 | IMD cyclonic storm 62, severe 89, very severe 118 |
| Heat | heat index °C (NOAA), latest | ≤38 | 41 | ~44 | 48 | ≥52 | IMD heatwave 40 / 45 / 47 |
| Visibility | m, min over the same 15-min set | ≥2000 | 1000 (0.2) | ~400 | 200 | ≤50 | IMD dense fog <200, very dense <50 |

- Rain factor = max(intensity, accumulation).
- Combined hazard `H = 1 − Π(1 − s)`.
- Historical conditions: ×1.15 when the dominant factor's input exceeds the
  city's p95 for the month (`climatology.json`); ×1.0 when climatology is
  absent.
- Forecast: `H = max(H, 0.8 × H_forecast_60min)`; `developing = true` when
  the forecast category is above the current one.
- Score = round(100 × min(H, 1)). Low <25 ≤ Medium <50 ≤ High <75 ≤
  Critical.
- The dominant factor names the hazard (`rain`, `wind`, `heat`, `fog`).
- The factor breakdown is returned for the UI.
- All anchors live in one dict. Calibration tests pin intent: Michaung peak
  (26 mm/h, 90 km/h gust) → Critical; ordinary Mumbai monsoon hour (5 mm/h)
  → Low; Delhi 46 °C dry → High; fog at 150 m → Critical; calm → Low.

**Impact** (`impact.py`):

- Each route sample point takes the maximum risk of stations whose radius
  covers it (sensor 10 km, reference 40 km), else an inverse-distance blend
  of the two nearest references within 150 km, else no coverage. Candidate
  stations per point are precomputed once (network and stations are static).
- Route status = max category along the route; exposure = share of points
  at High or above; affected when status ≥ High.
- Hub status = category at the hub.
- Active deliveries per route = capacity × time-of-day curve (IST, by
  `observed_at`) × seeded jitter. Last-mile capacity 20–45, busy
  11:00–19:00, near zero at night. Linehaul 4–12 trucks, heavier overnight.
- KPIs: routes affected (linehaul, last-mile), deliveries at risk, hubs
  affected, locations at High or above, active incidents.
- Reroute: Dijkstra over the corridor graph excluding affected corridors;
  suggested when every corridor on the path is below High and the detour is
  at most 2× the direct corridor.

**Incidents** (`incidents.py`): key = (city region, hazard). A region is a
reference city with its hubs, sensors and last-mile routes; a linehaul route
belongs to the region nearest its highest-risk point.

- Open when region category ≥ High on 2 consecutive engine ticks that saw
  fresh data for the region (debounces a single noisy window in any mode).
- Escalate at Critical; de-escalate back to High.
- Resolve when below Medium for 10 observed minutes.
- Reopening within 30 minutes continues the same incident.
- Each incident carries id, region, hazard, status, opened/resolved times,
  current and peak score, affected route/hub ids, deliveries at risk
  (current and peak), actions and a timeline of status changes with reasons.

**Actions** (`actions.py`): declarative rules keyed on (hazard, minimum
category, asset kind) → text template, owner, priority (P1 Critical, P2
High, P3 pre-alert), assets and the values that triggered it. Rules:

| Hazard | Category | Assets | Action | Owner |
|---|---|---|---|---|
| rain | High | last-mile | Pause two-wheeler dispatch in {region} ({n} routes, {d} deliveries); notify customers of delay | last-mile ops |
| rain | Critical | hub | Waterlogging SOP at {hub}: raise ground-level stock, divert inbound to {alt_hub} | warehouse |
| any | High | linehaul | Hold departures on {route} for 60 min; reroute via {path} (+{km} km) when available | linehaul |
| wind | High | any | Notify drivers on {routes}: gusts {g} km/h, reduce speed, no high-sided loads | fleet |
| fog | High | linehaul | Delay departures until visibility > 500 m; convoy on {routes} | linehaul |
| heat | High | last-mile | Shift slots to before 11:00 / after 16:00 in {region}; check cold-chain loads | last-mile ops |
| any | developing | any | Pre-alert: {hazard} expected in {region} within 60 min; stage drivers, hold new dispatch | dispatch |

Recommendations only; nothing is sent.

**Outputs.** Redis keys and pub/sub messages per the contracts; incident
changes and per-minute snapshots to `weather-decisions`; `health:engine`
with consumer lag (end offset − position), end-to-end latency (now −
`event_time` of newest reading in each window) and freshness per source
kind; `dq:summary` from quarantine records, Spark drop counters, `seq` gaps
and stale sensors.

Deployment: ConfigMap from source (`serving/engine.py`, `weatherops/`,
model artifacts) on `python:3.12-slim`; installs `libgomp1` (LightGBM needs
OpenMP) and `pip install kafka-python-ng redis lightgbm numpy`.

### 5. Forecasting (`ml/` offline, `weatherops/forecast.py` online)

- Data: Historical Forecast API, 40 cities, hourly, 2022-01-01 to
  2025-12-31, the seven live variables. About 1.4 M rows, ~4,200 calls once.
  Cached as Parquet in `ml/data/` (gitignored). The fetch reports per
  variable null rates.
- `climatology.json`: p50/p95 per city per month for rain intensity, gust,
  heat index, and p5 for visibility. Written to `weatherops/`.
- Split by time: train 2022-01 → 2024-06, validate 2024-07 → 2024-12, test
  2025.
- Four LightGBM regressors at +60 min: rain (Tweedie), gust (L2),
  temperature (L2), log visibility (L2). Heat index forecast = predicted
  temperature with current humidity.
- Features, all at or before t: current values; 1/2/3 h lags; 1 h and 3 h
  deltas (3 h pressure tendency); rain sum 3 h and 24 h; local hour and day
  of year as sin/cos; lat, lon.
- Not used: neighbouring-city features (upgrade path), NWP forecasts as
  inputs (no leakage-free archive), deep learning.
- Evaluation in `ml/report.md` (committed): per target MAE and skill = 1 −
  MAE/MAE_persistence; rain MAE on rainy hours; onset detection — below High
  now, High or above in 60 min — precision and recall (persistence scores
  zero recall by definition).
- Ship rule: a target ships only if test skill > 0; otherwise production
  uses persistence for it and the model card says so.
- Artifacts: gzipped LightGBM text models + `model_card.json` in
  `ml/artifacts/` (committed), mounted into the engine by ConfigMap.
- Serving: city-level predictions refreshed when new reference data arrives.
  A lag value at t − k h is interpolated linearly between the buffered
  window midpoints that bracket it; NaN when no window lies within 90
  observed minutes (LightGBM handles NaN). Sensors inherit their city's
  forecast.
- Horizon is +60 min only; the training data is hourly.
- Parity: training builds features vectorised in pandas, serving per city;
  `test_feature_parity` asserts both agree on sampled rows.

### 6. API (`serving/api.py`)

FastAPI on `python:3.12-slim` with `redis` (asyncio client), `fastapi`,
`uvicorn`, `websockets`. One Redis pub/sub subscription per process fans
out to connected WebSocket clients. Each client gets `snapshot` on connect.
Slow clients are dropped (bounded send queue) rather than blocking others.
Health status thresholds: engine tick older than 30 s → degraded, 120 s →
down; a Spark query's progress older than 2 × its trigger + 30 s →
degraded, key missing → down; live reference data older than 30 min →
degraded.

### 7. Dashboard (`dashboard/`)

Kept: Next.js, Vercel, IBM Plex type, light/dark tokens. Removed: the proxy
route (direct HTTPS/WSS now), the station card grid, threshold copy.

- `NEXT_PUBLIC_API_HOST` names the cluster host. `useFeed()` loads
  `/api/snapshot`, then holds `wss://host/ws` with exponential backoff. If
  the host is unset or unreachable for 15 s it plays
  `public/recordings/<event>.json` on a loop, clearly labelled "Recording".
- Layout: header with mode badge (LIVE / REPLAY event + weather clock / SIM
  / RECORDING) and connection state; KPI strip (active incidents, locations
  High+, routes affected, deliveries at risk, hubs affected); India map;
  incident feed with recommended actions; selected-location detail (values,
  factor breakdown, +60 min forecast, sensor verdict); data quality and
  pipeline health panel. Stacks on phones.
- Map: `maplibre-gl` with a style built from local GeoJSON only — no tile
  server, no API key. Basemap `public/india.geojson` from Natural Earth
  (public domain) using India's point-of-view boundaries;
  `public/network.geojson` exported from `weatherops.network`. Layers:
  routes coloured by status, sized by active deliveries; stations coloured
  by category (sensors small, references large, suspect sensors outlined);
  hubs; incident halos.
- Category colours validated for colour-vision deficiency in both themes
  (dataviz skill). Category is never shown by colour alone.
- Footer credits Open-Meteo (CC BY 4.0) and Natural Earth.
- `dashboard/scripts/record.mjs` (Node built-in WebSocket, no deps) records
  a replay from the WebSocket into `public/recordings/<event>.json`, ticks
  downsampled to at most one per 5 s, target under 3 MB.

### 8. Infrastructure and deploy

Terraform (`infra/`):

- Security group: keep 22 (operator IP). Add 80 and 443 (Traefik, ACME
  HTTP-01). Remove 9094 (Kafka external; Databricks is gone and PLAINTEXT
  Kafka should not face the internet) and 30080 (API now behind ingress).
- On demand: `node_enabled` variable; `aws_instance` and a new
  `aws_eip_association` get `count`; `aws_eip` stands alone and persists.
  `moved` blocks keep existing state addresses. Outputs read from the EIP.
- Remove the `kafka_bootstrap` output and `verify-external-kafka.sh`.

`bootstrap.sh` additionally installs cert-manager (pinned chart, CRDs
enabled) and a Let's Encrypt `ClusterIssuer` (HTTP-01 through Traefik).

`deploy.sh`:

| Command | Effect |
|---|---|
| `./deploy.sh` | node up, wait for k3s, bootstrap, topics, apps, ingress if `WEATHEROPS_HOST` is set, restore TLS secret |
| `./deploy.sh --apps` | redeploy application code only |
| `./deploy.sh --live` / `--replay <event> [speed]` / `--sim <scenario>` | switch ingestor mode |
| `./deploy.sh --tunnel` | SSH-forward the API to `localhost:8000` |
| `./deploy.sh --status` | Terraform outputs, pods, health |
| `./deploy.sh --down` | back up the TLS secret to `~/.weatherops/`, destroy the node only |
| `./deploy.sh --destroy` | destroy everything (warns that S3 must be emptied first) |

The TLS secret backup keeps repeated bring-ups under Let's Encrypt's limit
of 5 certificates per exact name per week. `WEATHEROPS_HOST` is the DuckDNS
name the operator created once and pointed at the Elastic IP.

## Security

- Public surface: 22 (operator IP only) and 80/443 (read-only API and
  WebSocket, TLS). Kafka and Redis are cluster-internal.
- The API serves derived weather and synthetic logistics data only. No
  secrets, no writes, no auth needed.
- AWS keys reach pods only through Kubernetes Secrets created over stdin
  (existing pattern). Redis stays password-protected.

## Testing

- `pytest` per package, all pure logic without Kafka, Redis or Spark:
  `weatherops/tests`, `ingestor/tests`, `serving/tests` (engine with
  in-memory fakes, API with a fake Redis), `ml/tests`.
- Spark plan functions run in batch mode inside `apache/spark:3.5.3-python3`
  via Docker Desktop; fallback is a smoke run on the cluster.
- Dashboard: `next build` passes; recording mode checked in a browser.
- Cluster end-to-end: a sim scenario with a storm crossing a known hub opens
  an incident with the expected actions, pushed over the WebSocket.

## Phases

Each phase ends deployable and committed.

1. **Foundation + ingestion** — `weatherops` (geo, schema, network),
   `ingestor` (live, replay, sim, sensors, faults), topics in `deploy.sh`,
   delete `producer/` and `databricks/`.
2. **Spark v2** — `plans.py`, validation, dedup, windows, quarantine,
   listener, decisions sink.
3. **Risk engine** — risk, anomaly, impact, incidents, actions, engine loop,
   Redis state, decisions topic.
4. **Serving + dashboard + infra** — API and WebSocket, SG and on-demand
   Terraform, cert-manager and ingress, dashboard rebuild, recording player.
5. **Forecasting** — fetch, climatology, training, evaluation, artifacts,
   engine integration, developing pre-alerts.
6. **Ship** — replay catalogue finalised from data, recording captured,
   WeatherOps README and architecture, model card in the dashboard.

## Verification (definition of done)

1. All `pytest` suites pass locally; Spark plan tests pass in the Spark
   container or on the cluster.
2. `./deploy.sh` brings the node up from `--down` without manual steps
   beyond `WEATHEROPS_HOST`.
3. In `--sim` with a storm over a hub: features flow, an incident opens with
   actions, sensor faults appear in the data-quality panel as
   `sensor_suspect`, and the hub micro-event is classified `weather_event`.
4. `--replay` of a cyclone event raises High/Critical risk along NH16 and
   reports routes affected and deliveries at risk.
5. The dashboard updates over the WebSocket without refresh, and plays the
   committed recording when the cluster is down.
6. `ml/report.md` reports skill against persistence per target and onset
   precision/recall on 2025, whatever the numbers are.
7. S3 holds `raw/`, `quarantine/`, `features/`, `decisions/` partitions for
   the run date.

## Open follow-ups

- Corridors are straight segments; snap to roads (OSRM) if accuracy matters.
- Neighbouring-city features for the forecast model.
- Postgres if acknowledge/assign workflows are added.
- One engine instance; partition by region if the station count grows by an
  order of magnitude.
- Open-Meteo free tier is non-commercial; a commercial deployment needs a
  paid plan or another provider.
