# WeatherOps — Weather-Aware E-commerce Delivery Intelligence (design)

Date: 2026-10-02 · Status: approved by the user ("You have approval to implement the full
system end-to-end, deploy it, and verify it") · Supersedes the live-weather product of
`2026-10-01-weatherops-design.md` (that infrastructure is reused).

## 1. Intent

An internal tool for **ShopFlow India** (synthetic e-commerce company) used by delivery
operations managers, logistics and supply-chain analysts. Loop: **Monitor → Analyze →
Predict → Simulate**. Within seconds a manager must see: what is happening now, where
rainfall is affecting deliveries, which upcoming orders are at risk and why, what happened
historically, and which operational change could reduce the impact. Weather is the input
layer; delivery and business impact are the product.

User decisions: replace the live-weather product but reuse its parts; PostgreSQL for
relational/historical data; rainfall is historical/simulated (no live weather); keep it
practical on the existing single node; aggregated history is fine; streaming is real
(Kafka → Spark → DB/API → UI); scenario analysis stays simple and believable; exactly four
pages; desktop only (1366–1920 px); visual language of the user's Kasauti project
(mplads-risk-monitor-web.vercel.app): Geist / Geist Mono, light grey surface, pill nav,
global search, dense editorial layout, restrained colour, subtle motion.

## 2. Architecture

```
weather baseline   Open-Meteo archive (ERA5) daily rain 2015-2025, 40 cities ─┐
replay weather     hourly rain 2022-2025 (already downloaded), sparse csv.gz ─┤
                                                                             ▼
seed job (once)    company master data + aggregated history 2021-01-01..sim start ─▶ PostgreSQL
simulator          sim clock replays a real monsoon window (default 2025-07-01, 60×),
                   order lifecycle + WEATHER_EVENT ─▶ Kafka shopflow-events
Spark              parse, validate → shopflow-quarantine, dedup within watermark,
                   clean ─▶ Kafka shopflow-clean, Postgres delivery_events (foreachBatch),
                   30 s windows per city ─▶ Kafka shopflow-metrics; listener ─▶ Redis
engine             consumes clean + metrics; live state of active & scheduled orders;
                   impact + future risk + alerts every 5 s ─▶ Redis snapshot + pub/sub,
                   Postgres orders/predictions/live history roll-up
API (FastAPI)      Redis (live) + Postgres (drill-down, future, history, scenario, search)
Dashboard (Next)   4 pages, data from API only; WS for live; committed demo snapshot as
                   fallback when the backend is down ("displaying last known state")
```

Retired: live Open-Meteo ingestion, the sensor/anomaly/incident engine, LightGBM nowcast,
old dashboard components (all remain in git history). Kept: Kafka/Redis/cert-manager
bootstrap, ConfigMap-from-source deploy, Spark listener → Redis health, Broadcaster/relay
WebSocket fan-out, Terraform on-demand node, Vercel deploy, India POV outline.

## 3. Company and network (`weatherops/company.py`, deterministic seed)

- 40 cities (existing list) with state, demand weight (population-like).
- 10 fulfilment centres (warehouses) at real logistics clusters: Bilaspur (DEL), Bhiwandi
  (MUM), Hoskote (BLR), Sriperumbudur (CHE), Shamshabad (HYD), Dankuni (KOL), Changodar
  (AMD), Chakan (PUN), Chinhat (LKO), Amingaon (GUW).
- 40 delivery hubs, one per city (25 existing named hubs + 15 city hubs).
- Routes: every city hub is served by its nearest warehouse (primary, ~75 % of volume) and
  its second-nearest (secondary). Code `BLR → MYS`. Road distance = haversine × 1.25
  (intra-city 35 km). Weather sensitivity 0.7–1.4 (ghat / flood-prone corridors high).
- 6 categories (Electronics, Fashion, Groceries, Home, Beauty, Books & Toys), ~300 SKUs,
  20k customers, ~600 vehicles (trucks at warehouses, vans and bikes at hubs).
- Normal ETA = warehouse dwell 1.5 h + distance / 45 km/h + hub dwell 1 h + last mile 2 h.
- SLA: Express promise = normal ETA × 1.3, Standard = + 24 h, Economy = + 48 h
  (mix 20/65/15 %).

## 4. Weather (`weatherops/rainfall.py`)

- Daily rain per city 2015–2025 (Open-Meteo archive, CC BY 4.0) → `weather_history`;
  climatology per city × month → `weather_climatology`: mean, median, p75, p90,
  P(rainy day ≥ 2.5 mm), P(heavy ≥ 64.5 mm), season (pre-monsoon Mar–May, monsoon Jun–Sep,
  post-monsoon Oct–Dec, winter Jan–Feb).
- Severity classes (IMD-based). Daily: none < 2.5, rain < 64.5, heavy < 115.6, extreme.
  Hourly: none < 1, rain < 7.6, heavy < 20, extreme.
- Simulator weather: real hourly rain of the replay window (sparse file). "Forecast" for the
  next 48 h = the replay's actual next hours blended with climatology and degraded by
  lead time (probability of class), labelled as forecast; truth drives actual delays.

## 5. Impact, risk, scenario (pure functions, unit-tested)

- `delay.py`: class multipliers none 1.00, rain 1.12, heavy 1.30, extreme 1.56;
  weather ETA = normal × (1 + (m − 1) × sensitivity); weather delay = weather ETA −
  normal ETA. Exposure along a route = worst class over origin, destination and nearest
  cities to sample points during the travel window. Costs (₹, labelled simulated):
  extra transport per delayed vehicle-hour, reshipping for failed deliveries (extreme rain
  failure rate), SLA compensation by tier.
- `risk.py`: score 0–100 = sum of explained contributions, each bounded:
  rainfall severity (expected class × probability, 0–30), historical route weather impact
  (route's historical delay ratio in that class, 0–20), route exposure (share of path and
  hours in rain, 0–15), warehouse/hub load (concentration of exposed orders, 0–10),
  delivery timing (dispatch inside peak-rain hours, 0–8), SLA tightness (slack vs expected
  delay, 0–12), historical route on-time performance (0–5). Category Low < 25 ≤ Medium < 50
  ≤ High < 75 ≤ Critical. Expected delay from §5 delay model with expected multiplier.
  SLA breach probability = logistic regression fitted (numpy IRLS) on a 200k-order
  synthetic history sample in the seed job; coefficients stored in the DB.
- `scenario.py`: over upcoming orders in a window, apply one or more levers —
  dispatch shift (−4…+4 h), reroute exposed orders to the secondary warehouse, add a
  temporary hub/micro-fulfilment in a city (orders served locally), rainfall severity
  multiplier (0.5–2×). Compare current vs simulated: late deliveries, average delay, SLA
  breaches, weather-exposed orders, estimated cost. Results saved to `simulation_runs` /
  `scenario_results`; always labelled "Simulated impact / Estimated outcome".

## 6. Data model (PostgreSQL 16, `db/schema.sql`)

Master: states, cities, warehouses, hubs, routes, categories, products, customers,
vehicles. Weather: weather_history (city, date, rain_mm, severity),
weather_climatology (city, month, stats). Operations: orders (live + scheduled, pruned
after 7 sim days), order_items, delivery_events (Spark sink, pruned after 2 days),
delivery_predictions (latest per order), stream_metrics (30 s windows). Analytics:
history_daily (date, route_id, category_id, severity, orders, exposed, affected, delayed,
sla_breaches, delay_min_sum, cost_transport, cost_reship, cost_sla) — synthetic history
2021-01-01 → sim start, plus live days rolled up by the engine; route_weather_impact
(route × severity: avg delay ratio, on-time rate) derived from history. Scenario:
simulation_runs, scenario_results. Model: model_coefficients. Indexes on every filter
column used by History and on orders(status, promised_at), orders(route_id).

Volume target: history_daily ≈ 0.5 M rows, orders ≤ ~150 k live rows; the browser only
ever receives aggregates or a page of rows.

## 7. Streaming

Event envelope: `{event_id, type, sim_time, emitted_at, order_id?, city_id?, route_id?,
payload}`. Types: ORDER_CREATED, ORDER_ASSIGNED, VEHICLE_DISPATCHED, VEHICLE_MOVEMENT,
HUB_ARRIVAL, DELIVERY_DELAY, DELIVERY_COMPLETED, WEATHER_EVENT (per city per sim hour),
RISK_UPDATE (engine → topic shopflow-risk, informational). Simulator rate: default 25 k
orders per sim day at 60× (≈ 17 orders/s, ≈ 80–100 events/s); ~0.3 % duplicate and ~0.1 %
invalid events so Spark's quarantine and dedup do real work. Health shown in the UI:
events/s, last processed event, freshness, consumer lag, Kafka / Spark / API / DB /
stream status.

## 8. API

`/api/health`, `/api/overview`, `/ws`, `/api/map?mode=rainfall|impact&month=`,
`/api/locations/{state|city|warehouse|hub|route}/{id}`, `/api/future?window=&risk=&page=`,
`/api/future/timeline`, `/api/orders/{id}`, `POST /api/scenario`, `/api/history?filters`,
`/api/history/options`, `/api/search?q=`. Read-mostly; the only write is scenario runs.

## 9. Frontend (Next.js 16, desktop only)

Shell: wordmark, pill nav `01 Overview · 02 Map · 03 Future · 04 History`, global search
(⌘K: order id, route, city, warehouse, hub), compact system status, "synthetic demo data"
label, footer with sim clock and data sources. Below 1200 px: "open on a desktop or
laptop" notice (like the reference). Geist + Geist Mono via next/font. Charts are hand-built
SVG following the dataviz skill (validated palette, risk scale Low/Medium/High/Critical
reused from the current product). Motion: 150–250 ms transitions on page, drawer, row
hover, KPI number tweens, map hover; nothing pulses.

- Overview: inline KPI row (Total orders, In transit, Weather exposed, Weather affected,
  Delayed, SLA risk, Avg delay, Weather impact cost); live operations strip; top regions /
  routes / warehouses tables; critical orders; plain-language alerts.
- Map: large MapLibre map, modes Rainfall (climatology/month) and Delivery Impact; state
  choropleth + city circles + warehouses/hubs + routes; hover tooltips; click drill-down
  India → state → city → warehouse/hub → route → orders in a side panel.
- Future: 6/12/24/48 h tabs, summary line, risk timeline (48 h), sortable/paginated risk
  table, order drawer with contribution breakdown, scenario panel (levers + current vs
  simulated).
- History: filter bar (year, month, state, city, warehouse, hub, route, category,
  severity); monthly trend, year-over-year, season comparison, geography and product
  breakdowns, rain severity vs delay and vs SLA breach.
- States: skeletons, empty, error, reconnecting, and fallback to the committed demo
  snapshot when the API is unreachable.

## 10. Deployment and verification

Postgres as a k3s StatefulSet (local-path PVC) with a generated password Secret; seed job
runs once (idempotent). docker-compose gains postgres + seed + simulator. Verification:
unit tests (pure modules), Spark plan tests in the Spark image, API tests against a real
Postgres where available, dashboard lib tests, local compose smoke run, cloud deploy,
browser check of all four pages at 1366 and 1920 px with no console errors, Vercel deploy.
