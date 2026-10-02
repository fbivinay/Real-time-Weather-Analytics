"""Seed job: schema, ShopFlow master data, rainfall baseline, synthetic history,
route weather impact and the SLA-breach model. Runs once; re-running is a
no-op unless SEED_VERSION changes (then history and model are rebuilt).

    PGHOST=... PGPASSWORD=... python -m seed.main
"""
import json
import logging
import os
from datetime import date, timedelta
from pathlib import Path

import psycopg

from seed import history
from weatherops import company as co
from weatherops.rainfall import climatology, daily_class, read_daily, season
from weatherops.replay import REPLAY_START

log = logging.getLogger("seed")
ROOT = Path(__file__).absolute().parent.parent   # not resolve(): the Job symlinks code into place
SEED_VERSION = "1"


def connect():
    return psycopg.connect(
        host=os.environ.get("PGHOST", "localhost"), port=int(os.environ.get("PGPORT", 5432)),
        user=os.environ.get("PGUSER", "weatherops"), password=os.environ.get("PGPASSWORD"),
        dbname=os.environ.get("PGDATABASE", "weatherops"), autocommit=False)


def copy(cur, table, columns, rows):
    with cur.copy(f"COPY {table} ({', '.join(columns)}) FROM STDIN") as cp:
        for row in rows:
            cp.write_row(row)


def master_data(cur):
    net = co.NETWORK
    cur.execute("TRUNCATE cities, warehouses, hubs, routes, categories, products, customers, vehicles CASCADE")
    copy(cur, "cities", ("id", "name", "state", "lat", "lon", "demand", "sensitivity"),
         [(c.id, c.name, c.state, c.lat, c.lon, co.DEMAND[c.id], co.CITY_SENSITIVITY.get(c.id, 1.0))
          for c in net.cities.values()])
    copy(cur, "warehouses", ("id", "name", "city_id", "lat", "lon"),
         [(w.id, w.name, w.city_id, w.lat, w.lon) for w in net.warehouses.values()])
    copy(cur, "hubs", ("id", "name", "city_id", "lat", "lon"),
         [(h.id, h.name, h.city_id, h.lat, h.lon) for h in net.hubs.values()])
    copy(cur, "routes", ("id", "code", "warehouse_id", "hub_id", "city_id", "role", "share", "distance_km",
                         "sensitivity", "normal_eta_h", "path"),
         [(r.id, r.code, r.warehouse_id, r.hub_id, r.city_id, r.role, r.share, r.distance_km,
           r.sensitivity, round(co.normal_eta_h(r), 2), list(r.cities)) for r in net.routes.values()])
    copy(cur, "categories", ("id", "name", "share", "aov_inr"), co.CATEGORIES)
    copy(cur, "products", ("id", "category_id", "name", "price_inr", "weight_kg"),
         [(p["id"], p["category_id"], p["name"], p["price"], p["weight_kg"]) for p in co.products()])
    copy(cur, "customers", ("id", "name", "city_id", "segment"),
         [(c["id"], c["name"], c["city_id"], c["segment"]) for c in co.customers()])
    copy(cur, "vehicles", ("id", "type", "base_id", "capacity"),
         [(v["id"], v["type"], v["base_id"], v["capacity"]) for v in co.vehicles()])


def weather(cur, rows):
    cur.execute("TRUNCATE weather_history, weather_climatology")
    copy(cur, "weather_history", ("city_id", "date", "rain_mm", "severity"),
         [(c, d, mm, daily_class(mm)) for c, d, mm in rows])
    clim = climatology(rows)
    copy(cur, "weather_climatology", ("city_id", "month", "season", "days", "mean_mm", "median_mm", "p75_mm",
                                      "p90_mm", "p_rainy", "p_heavy", "monthly_total_mm"),
         [(c, m, season(m), s["days"], s["mean_mm"], s["median_mm"], s["p75_mm"], s["p90_mm"],
           s["p_rainy"], s["p_heavy"], s["monthly_total_mm"]) for (c, m), s in clim.items()])


def history_tables(cur, rows):
    rain = {(c, d): mm for c, d, mm in rows}
    end = date.fromisoformat(REPLAY_START[:10])
    days = [date(2021, 1, 1) + timedelta(d) for d in range((end - date(2021, 1, 1)).days)]
    frame = history.build(days, rain, seed=7)
    cols = list(frame.columns)
    cur.execute("TRUNCATE history_daily, route_weather_impact")
    copy(cur, "history_daily", cols, frame.itertuples(index=False, name=None))
    impact = history.route_impact(frame)
    copy(cur, "route_weather_impact", list(impact.columns), impact.itertuples(index=False, name=None))
    log.info("history_daily: %d rows", len(frame))
    coefs, metrics = history.fit_breach_model(seed=1)
    cur.execute("INSERT INTO model_coefficients (name, coefs, metrics) VALUES ('sla_breach', %s, %s) "
                "ON CONFLICT (name) DO UPDATE SET coefs = EXCLUDED.coefs, metrics = EXCLUDED.metrics, "
                "fitted_at = now()", (json.dumps(coefs), json.dumps(metrics)))
    log.info("sla_breach model: %s %s", coefs, metrics)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    with connect() as conn, conn.cursor() as cur:
        cur.execute((ROOT / "db" / "schema.sql").read_text(encoding="utf-8"))
        cur.execute("SELECT value FROM seed_info WHERE key = 'version'")
        row = cur.fetchone()
        if row and row[0] == SEED_VERSION and not os.environ.get("SEED_FORCE"):
            log.info("already seeded (version %s)", SEED_VERSION)
            return
        rows = read_daily(ROOT / "data" / "rain_daily.csv.gz")
        master_data(cur)
        weather(cur, rows)
        history_tables(cur, rows)
        cur.execute("TRUNCATE orders, order_items, delivery_predictions, delivery_events, stream_metrics")
        cur.execute("INSERT INTO seed_info VALUES ('version', %s), ('seeded_at', now()::text) "
                    "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", (SEED_VERSION,))
        cur.execute("ANALYZE")
    log.info("seed complete")


if __name__ == "__main__":
    main()
