-- WeatherOps / ShopFlow India (synthetic demo data). Idempotent: safe to re-run.

CREATE TABLE IF NOT EXISTS cities (
  id text PRIMARY KEY, name text NOT NULL, state text NOT NULL,
  lat double precision NOT NULL, lon double precision NOT NULL,
  demand real NOT NULL, sensitivity real NOT NULL
);
CREATE INDEX IF NOT EXISTS cities_state ON cities (state);

CREATE TABLE IF NOT EXISTS warehouses (
  id text PRIMARY KEY, name text NOT NULL, city_id text NOT NULL REFERENCES cities,
  lat double precision NOT NULL, lon double precision NOT NULL
);

CREATE TABLE IF NOT EXISTS hubs (
  id text PRIMARY KEY, name text NOT NULL, city_id text NOT NULL REFERENCES cities,
  lat double precision NOT NULL, lon double precision NOT NULL
);

CREATE TABLE IF NOT EXISTS routes (
  id text PRIMARY KEY, code text NOT NULL UNIQUE,
  warehouse_id text NOT NULL REFERENCES warehouses, hub_id text NOT NULL REFERENCES hubs,
  city_id text NOT NULL REFERENCES cities, role text NOT NULL, share real NOT NULL,
  distance_km real NOT NULL, sensitivity real NOT NULL, normal_eta_h real NOT NULL,
  path text[] NOT NULL
);
CREATE INDEX IF NOT EXISTS routes_city ON routes (city_id);
CREATE INDEX IF NOT EXISTS routes_warehouse ON routes (warehouse_id);

CREATE TABLE IF NOT EXISTS categories (
  id text PRIMARY KEY, name text NOT NULL, share real NOT NULL, aov_inr real NOT NULL
);

CREATE TABLE IF NOT EXISTS products (
  id text PRIMARY KEY, category_id text NOT NULL REFERENCES categories,
  name text NOT NULL, price_inr real NOT NULL, weight_kg real NOT NULL
);

CREATE TABLE IF NOT EXISTS customers (
  id text PRIMARY KEY, name text NOT NULL, city_id text NOT NULL REFERENCES cities, segment text NOT NULL
);
CREATE INDEX IF NOT EXISTS customers_city ON customers (city_id);

CREATE TABLE IF NOT EXISTS vehicles (
  id text PRIMARY KEY, type text NOT NULL, base_id text NOT NULL, capacity int NOT NULL
);

-- Weather: daily rainfall 2015-2025 (Open-Meteo archive, CC BY 4.0) and its climatology.
CREATE TABLE IF NOT EXISTS weather_history (
  city_id text NOT NULL REFERENCES cities, date date NOT NULL,
  rain_mm real, severity text NOT NULL,
  PRIMARY KEY (city_id, date)
);
CREATE INDEX IF NOT EXISTS weather_history_date ON weather_history (date);

CREATE TABLE IF NOT EXISTS weather_climatology (
  city_id text NOT NULL REFERENCES cities, month smallint NOT NULL, season text NOT NULL,
  days int NOT NULL, mean_mm real, median_mm real, p75_mm real, p90_mm real,
  p_rainy real, p_heavy real, monthly_total_mm real,
  PRIMARY KEY (city_id, month)
);

-- Current weather risk per city, written by the engine every tick.
CREATE TABLE IF NOT EXISTS weather_risk (
  city_id text PRIMARY KEY REFERENCES cities, sim_time timestamptz NOT NULL,
  rain_mmph real NOT NULL, severity text NOT NULL, impact_score real NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now()
);

-- Live and scheduled orders (simulated stream). Pruned after a few simulated days;
-- completed orders are rolled into history_daily first.
CREATE TABLE IF NOT EXISTS orders (
  id text PRIMARY KEY, customer_id text NOT NULL, city_id text NOT NULL, route_id text NOT NULL,
  warehouse_id text NOT NULL, hub_id text NOT NULL, category_id text NOT NULL, tier text NOT NULL,
  value_inr real NOT NULL, items smallint NOT NULL, status text NOT NULL,
  created_at timestamptz NOT NULL, planned_dispatch timestamptz NOT NULL, promised_at timestamptz NOT NULL,
  dispatched_at timestamptz, eta_at timestamptz, delivered_at timestamptz, vehicle_id text,
  weather_class text, delay_min real, sla_breached boolean, updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS orders_status_dispatch ON orders (status, planned_dispatch);
CREATE INDEX IF NOT EXISTS orders_route ON orders (route_id, status);
CREATE INDEX IF NOT EXISTS orders_city ON orders (city_id, status);
CREATE INDEX IF NOT EXISTS orders_delivered ON orders (delivered_at);

CREATE TABLE IF NOT EXISTS order_items (
  order_id text NOT NULL, product_id text NOT NULL, qty smallint NOT NULL, price_inr real NOT NULL,
  PRIMARY KEY (order_id, product_id)
);

-- Clean event log written by Spark (foreachBatch JDBC append). No key: the
-- stream is deduplicated upstream, and a late duplicate must not fail a batch.
CREATE TABLE IF NOT EXISTS delivery_events (
  event_id text NOT NULL, type text NOT NULL, sim_time timestamptz NOT NULL, emitted_at timestamptz,
  order_id text, city_id text, route_id text, payload text, kafka_ts timestamptz
);
ALTER TABLE delivery_events ADD COLUMN IF NOT EXISTS emitted_at timestamptz;
CREATE INDEX IF NOT EXISTS delivery_events_order ON delivery_events (order_id);
CREATE INDEX IF NOT EXISTS delivery_events_time ON delivery_events (sim_time);

-- 30-second stream windows per city (Spark).
CREATE TABLE IF NOT EXISTS stream_metrics (
  window_start timestamptz NOT NULL, window_end timestamptz NOT NULL, city_id text NOT NULL,
  events bigint NOT NULL, created bigint NOT NULL, dispatched bigint NOT NULL, completed bigint NOT NULL,
  delays bigint NOT NULL, avg_delay_min double precision, max_sim_time timestamptz
);
CREATE INDEX IF NOT EXISTS stream_metrics_window ON stream_metrics (window_start);

-- Latest risk per upcoming-order cohort (route x tier x dispatch hour); orders join on these keys.
CREATE TABLE IF NOT EXISTS delivery_predictions (
  route_id text NOT NULL, tier text NOT NULL, dispatch_hour timestamptz NOT NULL,
  orders int NOT NULL, score real NOT NULL, category text NOT NULL, expected_class text NOT NULL,
  p_rain real NOT NULL, expected_delay_min real NOT NULL, p_breach real NOT NULL,
  contributions jsonb NOT NULL, computed_at timestamptz NOT NULL,
  PRIMARY KEY (route_id, tier, dispatch_hour)
);
CREATE INDEX IF NOT EXISTS delivery_predictions_category ON delivery_predictions (category, dispatch_hour);

-- Daily delivery history by route x category (synthetic 2021 -> simulation start,
-- then live days rolled up by the engine). Costs are simulated estimates (INR).
CREATE TABLE IF NOT EXISTS history_daily (
  date date NOT NULL, route_id text NOT NULL REFERENCES routes, category_id text NOT NULL REFERENCES categories,
  severity text NOT NULL, rain_mm real,
  orders int NOT NULL, exposed int NOT NULL, affected int NOT NULL, delayed int NOT NULL,
  sla_breaches int NOT NULL, delay_min_sum real NOT NULL,
  cost_transport real NOT NULL, cost_reship real NOT NULL, cost_sla real NOT NULL,
  PRIMARY KEY (date, route_id, category_id)
);
CREATE INDEX IF NOT EXISTS history_daily_route ON history_daily (route_id, date);
CREATE INDEX IF NOT EXISTS history_daily_category ON history_daily (category_id, date);
CREATE INDEX IF NOT EXISTS history_daily_severity ON history_daily (severity, date);

-- Monthly roll-up the History page and the map read (history_daily is ~0.8 M rows;
-- this is ~50 k). Refreshed by the seed job and by the engine every few minutes.
CREATE MATERIALIZED VIEW IF NOT EXISTS history_monthly AS
SELECT date_trunc('month', date)::date AS month, route_id, category_id, severity,
       sum(orders)::bigint AS orders, sum(exposed)::bigint AS exposed, sum(affected)::bigint AS affected,
       sum(delayed)::bigint AS delayed, sum(sla_breaches)::bigint AS sla_breaches,
       sum(delay_min_sum)::double precision AS delay_min_sum, sum(cost_transport)::double precision AS cost_transport,
       sum(cost_reship)::double precision AS cost_reship, sum(cost_sla)::double precision AS cost_sla,
       min(date) AS first_day, max(date) AS last_day
FROM history_daily GROUP BY 1, 2, 3, 4;
CREATE UNIQUE INDEX IF NOT EXISTS history_monthly_key ON history_monthly (month, route_id, category_id, severity);

CREATE OR REPLACE VIEW weather_impact AS
SELECT route_id, severity, count(DISTINCT date) AS days, sum(orders) AS orders, sum(exposed) AS exposed,
       sum(affected) AS affected, sum(delay_min_sum) AS delay_min_sum,
       sum(cost_transport + cost_reship + cost_sla) AS weather_cost
FROM history_daily GROUP BY route_id, severity;

CREATE TABLE IF NOT EXISTS route_weather_impact (
  route_id text NOT NULL REFERENCES routes, severity text NOT NULL,
  days int NOT NULL, delay_ratio real NOT NULL, on_time real NOT NULL,
  PRIMARY KEY (route_id, severity)
);

CREATE TABLE IF NOT EXISTS model_coefficients (
  name text PRIMARY KEY, coefs jsonb NOT NULL, metrics jsonb NOT NULL, fitted_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS simulation_runs (
  id bigserial PRIMARY KEY, created_at timestamptz NOT NULL DEFAULT now(), sim_time timestamptz,
  window_h int NOT NULL, levers jsonb NOT NULL
);

CREATE TABLE IF NOT EXISTS scenario_results (
  run_id bigint NOT NULL REFERENCES simulation_runs ON DELETE CASCADE, variant text NOT NULL,
  metrics jsonb NOT NULL, PRIMARY KEY (run_id, variant)
);

CREATE TABLE IF NOT EXISTS seed_info (
  key text PRIMARY KEY, value text NOT NULL
);
