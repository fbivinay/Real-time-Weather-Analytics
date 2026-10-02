# WeatherOps — Weather-Aware Delivery Intelligence

**Live:** https://dashboard-eight-iota-93.vercel.app · desktop and laptop only (1366 px and wider)

WeatherOps is an internal tool for **ShopFlow India**, a fictional e-commerce company, used by
delivery operations managers and logistics analysts. It answers, within seconds:

| Question | Where |
|---|---|
| What is happening right now? | **01 Overview** — live KPIs, alerts, rain-hit regions, critical orders |
| Where is rainfall affecting deliveries? | **02 Map** — India heat map (Delivery impact / Historical rainfall), drill-down India → state → city → warehouse/hub → route → orders |
| Which upcoming orders are at risk, and why? | **03 Future** — 6/12/24/48 h risk table, 48 h timeline, per-order explanation |
| What could reduce the impact? | **03 Future** — scenario simulation (dispatch shift, reroute, micro-hub, rain severity) |
| What happened historically? | **04 History** — monthly, yearly, seasonal, geographic, product and rain-severity analysis |

Loop: **Monitor → Analyze → Predict → Simulate**. All customers, orders, routes and costs are
synthetic demo data. Rainfall is real: Open-Meteo archive (ERA5) daily rain 2015–2025 and hourly
rain 2022–2025 for 40 Indian cities (CC BY 4.0).

## Architecture

```
seed job (once)   ShopFlow master data · 2015-2025 rainfall + climatology · synthetic history
                  2021 → Jul 2025 (0.8 M route-day rows, 26 M orders) · SLA-breach model ─▶ PostgreSQL

simulator ──▶ Kafka shopflow-events          orders, assignments, dispatches, vehicle movement,
              (~50-100 events/s)              hub arrivals, delays, deliveries, hourly weather;
                                              replays real Jul-Aug 2025 rain at 60x; injects
                                              duplicates and invalid events
Spark Structured Streaming ─┬─ invalid ─▶ shopflow-quarantine
                            ├─ valid, deduplicated ─▶ shopflow-clean + Postgres delivery_events
                            ├─ 30 s windows per city ─▶ shopflow-metrics + Postgres stream_metrics
                            └─ raw ─▶ S3 (data lake)          progress listener ─▶ Redis (health)
engine  live operations state · weather → delay impact · future-order risk · alerts
        ─▶ Redis (views + pub/sub) and Postgres (orders, predictions, weather risk, live history)
FastAPI REST + WebSocket ─▶ Next.js dashboard (Vercel); committed demo snapshot when offline
```

One on-demand k3s node on AWS (Terraform), Traefik + Let's Encrypt (`35-170-210-110.sslip.io`).

## Models (all explainable, all in `weatherops/`)

- **Weather → delay** (`delay.py`): weather ETA = normal ETA × (1 + (m − 1) × route sensitivity),
  m = 1.00 / 1.12 / 1.30 / 1.56 for dry / rain / heavy / extreme (IMD-based classes). BLR → MYS:
  5.8 h normal, 6.5 h rain, 7.6 h heavy, 9.1 h extreme. Weather-induced delay = weather ETA − normal ETA.
- **Future-order risk** (`risk.py`): 0–100 = rainfall severity (≤30) + historical route impact (≤20)
  + route exposure (≤15) + warehouse load (≤10) + delivery timing (≤8) + SLA tightness (≤12)
  + route on-time history (≤5); every point carries a sentence. Low < 25 ≤ Medium < 50 ≤ High < 75 ≤ Critical.
- **SLA breach probability**: logistic regression fitted (numpy IRLS) on 200 k simulated orders that
  only see a lead-time-degraded forecast, never the truth.
- **Forecast**: the replay's real future rain, degraded with lead time (≈90 % hit rate at 1 h, 50 % at 48 h).
- **Scenarios** (`scenario.py`): the same models re-applied to upcoming order cohorts with levers;
  results are labelled *simulated impact / estimated outcome* and saved to `simulation_runs`.
- **Map impact index**: 60 % live risk of the next 12 h + 40 % monsoon history (weather cost per order).

## Data model (PostgreSQL, `db/schema.sql`)

cities, warehouses, hubs, routes, categories, products, customers, vehicles · weather_history,
weather_climatology, weather_risk · orders, order_items, delivery_events, stream_metrics,
delivery_predictions · history_daily (+ `history_monthly` materialized view), route_weather_impact,
weather_impact view · model_coefficients · simulation_runs, scenario_results.

## Run it

```bash
WEATHEROPS_HOST=35-170-210-110.sslip.io TF_AUTO_APPROVE=1 ./deploy.sh   # node, Kafka, Redis, Postgres, seed, apps
./deploy.sh --status
./deploy.sh --restart-replay      # start the simulated operations over
./deploy.sh --down                # stop paying for the node (Elastic IP, S3, IAM stay)
```

Dashboard: `cd dashboard && NEXT_PUBLIC_API_HOST=35-170-210-110.sslip.io npm run dev`.
Refresh the offline snapshot: `API=https://35-170-210-110.sslip.io npm run capture-demo`.
Rainfall baseline: `python -m seed.fetch_rain` (≈25 min, Open-Meteo rate limits).

## Tests

```bash
pytest                                   # models, seed, simulator, engine, API (88 tests)
TEST_PG_DSN="host=... dbname=weatherops ..." pytest serving/tests/test_queries_pg.py   # SQL on live data
cd dashboard && npm test
# Spark plans (Python 3.8, in the Spark image):
MSYS_NO_PATHCONV=1 docker run --rm --user 0 -v "$PWD:/repo" -w /repo \
  -e PYTHONPATH=/opt/spark/python:/opt/spark/python/lib/py4j-0.10.9.7-src.zip:/repo \
  apache/spark:3.5.3-python3 bash -c "pip install -q pytest && python3 -m pytest -q spark_processor/tests"
```

Design: `docs/superpowers/specs/2026-10-02-shopflow-delivery-intelligence-design.md`.
