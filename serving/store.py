"""Postgres writes of the engine (psycopg 3). One connection, autocommit off;
each method is one transaction."""
import json
import os

import psycopg

from weatherops.replay import REPLAY_START

ORDER_COLS = ("id", "customer_id", "city_id", "route_id", "warehouse_id", "hub_id", "category_id", "tier",
              "value_inr", "items", "status", "created_at", "planned_dispatch", "promised_at", "dispatched_at",
              "eta_at", "delivered_at", "vehicle_id", "weather_class", "delay_min", "sla_breached")
UPSERT_ORDER = (
    f"INSERT INTO orders ({', '.join(ORDER_COLS)}) VALUES ({', '.join(['%s'] * len(ORDER_COLS))}) "
    "ON CONFLICT (id) DO UPDATE SET " + ", ".join(f"{c} = EXCLUDED.{c}" for c in ORDER_COLS[10:])
    + ", updated_at = now()"
)
ROLLUP = """
INSERT INTO history_daily (date, route_id, category_id, severity, rain_mm, orders, exposed, affected, delayed,
  sla_breaches, delay_min_sum, cost_transport, cost_reship, cost_sla)
VALUES (%s, %s, %s, %s, NULL, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (date, route_id, category_id) DO UPDATE SET
  severity = CASE WHEN array_position(ARRAY['none','rain','heavy','extreme'], EXCLUDED.severity)
                     > array_position(ARRAY['none','rain','heavy','extreme'], history_daily.severity)
                  THEN EXCLUDED.severity ELSE history_daily.severity END,
  orders = history_daily.orders + EXCLUDED.orders, exposed = history_daily.exposed + EXCLUDED.exposed,
  affected = history_daily.affected + EXCLUDED.affected, delayed = history_daily.delayed + EXCLUDED.delayed,
  sla_breaches = history_daily.sla_breaches + EXCLUDED.sla_breaches,
  delay_min_sum = history_daily.delay_min_sum + EXCLUDED.delay_min_sum,
  cost_transport = history_daily.cost_transport + EXCLUDED.cost_transport,
  cost_reship = history_daily.cost_reship + EXCLUDED.cost_reship,
  cost_sla = history_daily.cost_sla + EXCLUDED.cost_sla
"""
ROLLUP_FIELDS = ("orders", "exposed", "affected", "delayed", "sla_breaches", "delay_min_sum",
                 "cost_transport", "cost_reship", "cost_sla")


def connect(autocommit=False):
    return psycopg.connect(
        host=os.environ.get("PGHOST", "postgres"), port=int(os.environ.get("PGPORT", 5432)),
        user=os.environ.get("PGUSER", "weatherops"), password=os.environ.get("PGPASSWORD"),
        dbname=os.environ.get("PGDATABASE", "weatherops"), autocommit=autocommit, connect_timeout=10)


class Store:
    def __init__(self, conn):
        self.conn = conn

    def load_model(self):
        with self.conn.cursor() as cur:
            cur.execute("SELECT coefs FROM model_coefficients WHERE name = 'sla_breach'")
            coefs = cur.fetchone()[0]
            cur.execute("SELECT route_id, severity, delay_ratio, on_time FROM route_weather_impact")
            hist = {}
            for route_id, sev, ratio, on_time in cur.fetchall():
                hist.setdefault(route_id, {})[sev] = {"delay_ratio": ratio, "on_time": on_time}
        self.conn.commit()
        full = {}
        for rid, h in hist.items():
            # Classes a route never saw in five years fall back to the model.
            if len(h) == 4:
                full[rid] = h
        return coefs, full

    def write(self, orders, items, gone, rollup, weather, sim_now, predictions=None):
        with self.conn.transaction(), self.conn.cursor() as cur:
            if orders:
                cur.executemany(UPSERT_ORDER, [tuple(o[c] for c in ORDER_COLS) for o in orders])
            if items:
                cur.executemany("INSERT INTO order_items VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING", items)
            if gone:
                cur.execute("DELETE FROM order_items WHERE order_id = ANY(%s)", (gone,))
                cur.execute("DELETE FROM orders WHERE id = ANY(%s)", (gone,))
            if rollup:
                cur.executemany(ROLLUP, [(k[0], k[1], k[2], sev, *(round(v.get(f, 0), 2) for f in ROLLUP_FIELDS))
                                         for k, v, sev in rollup])
            if weather:
                cur.executemany(
                    "INSERT INTO weather_risk (city_id, sim_time, rain_mmph, severity, impact_score) "
                    "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (city_id) DO UPDATE SET sim_time = EXCLUDED.sim_time, "
                    "rain_mmph = EXCLUDED.rain_mmph, severity = EXCLUDED.severity, "
                    "impact_score = EXCLUDED.impact_score, updated_at = now()",
                    [(cid, sim_now, w["rain_mmph"], w["severity"], w["score"]) for cid, w in weather.items()])
            if predictions is not None:
                cur.execute("DELETE FROM delivery_predictions")
                with cur.copy("COPY delivery_predictions (route_id, tier, dispatch_hour, orders, score, category, "
                              "expected_class, p_rain, expected_delay_min, p_breach, contributions, computed_at) "
                              "FROM STDIN") as cp:
                    for (route_id, tier, dispatch), a in predictions.items():
                        cp.write_row((route_id, tier, dispatch, a["orders"], a["score"], a["category"],
                                      a["expected_class"], a["p_rain"], a["expected_delay_min"], a["p_breach"],
                                      json.dumps(a["contributions"]), sim_now))

    def reset_live(self):
        """The replay wrapped: forget live days and live orders."""
        with self.conn.transaction(), self.conn.cursor() as cur:
            cur.execute("DELETE FROM history_daily WHERE date >= %s", (REPLAY_START[:10],))
            cur.execute("TRUNCATE orders, order_items, delivery_predictions")

    def prune_events(self, keep_hours=2):
        with self.conn.transaction(), self.conn.cursor() as cur:
            cur.execute("DELETE FROM delivery_events WHERE kafka_ts < now() - make_interval(hours => %s)",
                        (keep_hours,))
            cur.execute("DELETE FROM stream_metrics WHERE window_start < now() - make_interval(hours => %s)",
                        (keep_hours,))
