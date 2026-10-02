"""SQL behind the API. Every function takes an open psycopg connection and
returns plain dicts/lists; filters are always bound parameters."""
from psycopg.rows import dict_row

from weatherops.rainfall import SEASON_OF_MONTH

SEVERITIES = ("none", "rain", "heavy", "extreme")
SEASON_SQL = "CASE " + " ".join(
    f"WHEN extract(month FROM h.month) = {m} THEN '{s}'" for m, s in SEASON_OF_MONTH.items()) + " END"
WEATHER_COST = "(h.cost_transport + h.cost_reship + h.cost_sla)"
MEASURES = f"""
  sum(h.orders)::bigint AS orders, sum(h.exposed)::bigint AS exposed, sum(h.affected)::bigint AS affected,
  sum(h.delayed)::bigint AS delayed, sum(h.sla_breaches)::bigint AS sla_breaches,
  round((sum(h.delay_min_sum) / nullif(sum(h.affected), 0))::numeric, 1)::float AS avg_delay_min,
  round(sum({WEATHER_COST})::numeric)::float AS weather_cost
"""
FILTERS = {
    "year": "extract(year FROM h.month) = %s",
    "month": "extract(month FROM h.month) = %s",
    "state": "c.state = %s",
    "city": "r.city_id = %s",
    "warehouse": "r.warehouse_id = %s",
    "hub": "r.hub_id = %s",
    "route": "h.route_id = %s",
    "category": "h.category_id = %s",
    "severity": "h.severity = %s",
}
FROM = "FROM history_monthly h JOIN routes r ON r.id = h.route_id JOIN cities c ON c.id = r.city_id"


def rows(conn, sql, params=()):
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def one(conn, sql, params=()):
    out = rows(conn, sql, params)
    return out[0] if out else None


def where(filters):
    clauses, params = [], []
    for key, sql in FILTERS.items():
        value = filters.get(key)
        if value not in (None, ""):
            clauses.append(sql)
            params.append(int(value) if key in ("year", "month") else value)
    return ("WHERE " + " AND ".join(clauses)) if clauses else "", params


# ---- history ---------------------------------------------------------------
def history(conn, filters):
    w, p = where(filters)
    geo_level = ("route", "h.route_id", "r.code") if filters.get("city") else \
        ("city", "r.city_id", "c.name") if filters.get("state") else ("state", "c.state", "c.state")
    return {
        "totals": one(conn, f"SELECT {MEASURES}, min(h.first_day) AS first_day, max(h.last_day) AS last_day "
                            f"{FROM} {w}", p),
        "monthly": rows(conn, f"SELECT to_char(h.month, 'YYYY-MM') AS month, {MEASURES} "
                              f"{FROM} {w} GROUP BY 1 ORDER BY 1", p),
        "yearly": rows(conn, f"SELECT extract(year FROM h.month)::int AS year, {MEASURES}, "
                             f"(max(h.last_day) - min(h.first_day) + 1)::int AS days {FROM} {w} GROUP BY 1 ORDER BY 1", p),
        "seasons": rows(conn, f"SELECT {SEASON_SQL} AS season, {MEASURES} {FROM} {w} GROUP BY 1", p),
        "geography": {"level": geo_level[0], "rows": rows(
            conn, f"SELECT {geo_level[1]} AS id, min({geo_level[2]}) AS name, {MEASURES} {FROM} {w} "
                  f"GROUP BY 1 ORDER BY weather_cost DESC NULLS LAST LIMIT 25", p)},
        "categories": rows(conn, f"SELECT h.category_id AS id, min(k.name) AS name, {MEASURES} {FROM} "
                                 f"JOIN categories k ON k.id = h.category_id {w} GROUP BY 1 ORDER BY orders DESC", p),
        "severity": rows(conn, f"SELECT h.severity, {MEASURES}, "
                               f"round((sum(h.delay_min_sum) / nullif(sum(h.exposed), 0))::numeric, 1)::float "
                               f"AS delay_per_exposed_min, "
                               f"round((sum(h.sla_breaches)::numeric / nullif(sum(h.orders), 0)), 4)::float "
                               f"AS breach_rate {FROM} {w} GROUP BY 1", p),
    }


def history_options(conn):
    return {
        "years": [r["y"] for r in rows(conn, "SELECT DISTINCT extract(year FROM month)::int AS y "
                                             "FROM history_monthly ORDER BY 1")],
        "states": [r["state"] for r in rows(conn, "SELECT DISTINCT state FROM cities ORDER BY 1")],
        "cities": rows(conn, "SELECT id, name, state FROM cities ORDER BY name"),
        "warehouses": rows(conn, "SELECT id, name, city_id FROM warehouses ORDER BY name"),
        "hubs": rows(conn, "SELECT id, name, city_id FROM hubs ORDER BY name"),
        "routes": rows(conn, "SELECT id, code, city_id, warehouse_id, hub_id FROM routes ORDER BY code"),
        "categories": rows(conn, "SELECT id, name FROM categories ORDER BY name"),
        "severities": list(SEVERITIES),
    }


# ---- map -------------------------------------------------------------------
def rainfall_map(conn, month):
    cities = rows(conn, """
        SELECT c.id, c.name, c.state, c.lat, c.lon, w.mean_mm, w.median_mm, w.p75_mm, w.p90_mm, w.p_rainy,
               w.p_heavy, w.monthly_total_mm, w.season
        FROM cities c JOIN weather_climatology w ON w.city_id = c.id AND w.month = %s ORDER BY c.id""", (month,))
    states = rows(conn, """
        SELECT c.state AS id, round(avg(w.monthly_total_mm)::numeric, 1)::float AS monthly_total_mm,
               round(avg(w.p_rainy)::numeric, 4)::float AS p_rainy, round(avg(w.p_heavy)::numeric, 4)::float AS p_heavy,
               count(*)::int AS cities
        FROM cities c JOIN weather_climatology w ON w.city_id = c.id AND w.month = %s GROUP BY 1""", (month,))
    return {"month": month, "cities": cities, "states": states}


def structural_impact(conn):
    """Long-run weather exposure of each city's deliveries: monsoon affected
    share, weather delay per order, SLA sensitivity and order density."""
    return rows(conn, f"""
        SELECT r.city_id AS id, c.state,
               sum(h.orders)::bigint AS orders,
               round((sum(h.affected)::numeric / nullif(sum(h.orders), 0)), 4)::float AS affected_share,
               round((sum(h.delay_min_sum) / nullif(sum(h.orders), 0))::numeric, 2)::float AS delay_per_order_min,
               round((sum(h.sla_breaches)::numeric / nullif(sum(h.orders), 0)), 4)::float AS breach_rate,
               round((sum({WEATHER_COST}) / nullif(sum(h.orders), 0))::numeric, 2)::float AS cost_per_order
        FROM history_monthly h JOIN routes r ON r.id = h.route_id JOIN cities c ON c.id = r.city_id
        WHERE extract(month FROM h.month) BETWEEN 6 AND 9
        GROUP BY 1, 2""")


def location_history(conn, kind, id_):
    key = {"state": "state", "city": "city", "warehouse": "warehouse", "hub": "hub", "route": "route"}[kind]
    w, p = where({key: id_})
    return {
        "last_12_months": rows(conn, f"SELECT to_char(h.month, 'YYYY-MM') AS month, {MEASURES} {FROM} {w} "
                                     f"AND h.month > (SELECT max(month) FROM history_monthly) - interval '12 months' "
                                     f"GROUP BY 1 ORDER BY 1", p),
        "monsoon": one(conn, f"SELECT {MEASURES} {FROM} {w} AND extract(month FROM h.month) BETWEEN 6 AND 9", p),
    }


def climatology_months(conn, city_id):
    return rows(conn, "SELECT month, mean_mm, p_rainy, p_heavy, monthly_total_mm, season FROM weather_climatology "
                      "WHERE city_id = %s ORDER BY month", (city_id,))


# ---- future and orders -----------------------------------------------------
FUTURE_SORT = {"score": "p.score DESC", "dispatch": "o.planned_dispatch", "delay": "p.expected_delay_min DESC",
               "breach": "p.p_breach DESC", "value": "o.value_inr DESC"}
FUTURE_FILTERS = {"category": "p.category = %s", "city": "o.city_id = %s", "route": "o.route_id = %s",
                  "warehouse": "o.warehouse_id = %s", "tier": "o.tier = %s", "state": "c.state = %s",
                  "hub": "o.hub_id = %s"}


def future_orders(conn, sim_now, window_h, filters, sort="score", page=1, size=25):
    clauses = ["o.status IN ('scheduled', 'assigned')", "o.planned_dispatch >= %s",
               "o.planned_dispatch <= %s::timestamptz + make_interval(hours => %s)"]
    params = [sim_now, sim_now, window_h]
    for key, sql in FUTURE_FILTERS.items():
        if filters.get(key):
            clauses.append(sql)
            params.append(filters[key])
    if filters.get("q"):
        clauses.append("(o.id ILIKE %s OR r.code ILIKE %s)")
        params += [f"%{filters['q']}%"] * 2
    base = (f"FROM orders o JOIN delivery_predictions p ON p.route_id = o.route_id AND p.tier = o.tier "
            f"AND p.dispatch_hour = o.planned_dispatch JOIN routes r ON r.id = o.route_id "
            f"JOIN cities c ON c.id = o.city_id WHERE {' AND '.join(clauses)}")
    total = one(conn, f"SELECT count(*)::int AS n {base}", params)["n"]
    data = rows(conn, f"""
        SELECT o.id, r.code AS route, o.route_id, o.city_id, c.name AS city, o.warehouse_id, o.tier, o.category_id,
               o.value_inr, o.status, o.planned_dispatch, o.promised_at,
               o.planned_dispatch + make_interval(secs => r.normal_eta_h * 3600) AS eta_at,
               p.score, p.category, p.expected_class, p.p_rain, p.expected_delay_min, p.p_breach
        {base} ORDER BY {FUTURE_SORT.get(sort, FUTURE_SORT['score'])}, o.id
        LIMIT %s OFFSET %s""", [*params, size, (max(1, page) - 1) * size])
    return {"total": total, "page": page, "size": size, "rows": data}


def order_detail(conn, order_id):
    order = one(conn, """
        SELECT o.*, r.code AS route, r.distance_km, r.normal_eta_h, r.sensitivity, r.path, c.name AS city, c.state,
               w.name AS warehouse, hb.name AS hub, k.name AS category
        FROM orders o JOIN routes r ON r.id = o.route_id JOIN cities c ON c.id = o.city_id
        JOIN warehouses w ON w.id = o.warehouse_id JOIN hubs hb ON hb.id = o.hub_id
        JOIN categories k ON k.id = o.category_id WHERE o.id = %s""", (order_id,))
    if not order:
        return None
    order["items"] = rows(conn, "SELECT i.product_id, p.name, i.qty, i.price_inr FROM order_items i "
                                "JOIN products p ON p.id = i.product_id WHERE i.order_id = %s", (order_id,))
    order["prediction"] = one(conn, "SELECT * FROM delivery_predictions WHERE route_id = %s AND tier = %s "
                                    "AND dispatch_hour = %s", (order["route_id"], order["tier"],
                                                               order["planned_dispatch"]))
    order["events"] = rows(conn, "SELECT type, sim_time, payload FROM delivery_events WHERE order_id = %s "
                                 "ORDER BY sim_time", (order_id,))
    order["route_history"] = rows(conn, "SELECT severity, days, delay_ratio, on_time FROM route_weather_impact "
                                        "WHERE route_id = %s", (order["route_id"],))
    return order


def route_orders(conn, route_id, limit=25):
    return rows(conn, """
        SELECT o.id, o.tier, o.status, o.planned_dispatch, o.promised_at, p.score, p.category,
               p.expected_delay_min, p.p_breach
        FROM orders o LEFT JOIN delivery_predictions p ON p.route_id = o.route_id AND p.tier = o.tier
             AND p.dispatch_hour = o.planned_dispatch
        WHERE o.route_id = %s AND o.status IN ('scheduled', 'assigned', 'in_transit', 'at_hub')
        ORDER BY p.score DESC NULLS LAST, o.planned_dispatch LIMIT %s""", (route_id, limit))


def scenario_cohorts(conn, sim_now, window_h):
    return rows(conn, """
        SELECT route_id, city_id, tier, planned_dispatch AS dispatch, count(*)::int AS count
        FROM orders WHERE status IN ('scheduled', 'assigned') AND planned_dispatch >= %s
          AND planned_dispatch <= %s::timestamptz + make_interval(hours => %s)
        GROUP BY 1, 2, 3, 4""", (sim_now, sim_now, window_h))


def save_scenario(conn, sim_now, window_h, levers, current, simulated):
    import json
    with conn.cursor() as cur:
        cur.execute("INSERT INTO simulation_runs (sim_time, window_h, levers) VALUES (%s, %s, %s) RETURNING id",
                    (sim_now, window_h, json.dumps(levers)))
        run_id = cur.fetchone()[0]
        cur.executemany("INSERT INTO scenario_results VALUES (%s, %s, %s)",
                        [(run_id, "current", json.dumps(current)), (run_id, "simulated", json.dumps(simulated))])
    return run_id


def search(conn, q, limit=6):
    like = f"%{q}%"
    return {
        "orders": rows(conn, "SELECT id, route_id, city_id, status FROM orders WHERE id ILIKE %s "
                             "ORDER BY id LIMIT %s", (like, limit)),
        "routes": rows(conn, "SELECT id, code, city_id FROM routes WHERE code ILIKE %s OR replace(code, ' → ', '-') "
                             "ILIKE %s OR id ILIKE %s ORDER BY code LIMIT %s", (like, like, like, limit)),
        "cities": rows(conn, "SELECT id, name, state FROM cities WHERE name ILIKE %s OR id ILIKE %s "
                             "OR state ILIKE %s ORDER BY name LIMIT %s", (like, like, like, limit)),
        "warehouses": rows(conn, "SELECT id, name, city_id FROM warehouses WHERE name ILIKE %s OR id ILIKE %s "
                                 "LIMIT %s", (like, like, limit)),
        "hubs": rows(conn, "SELECT id, name, city_id FROM hubs WHERE name ILIKE %s OR id ILIKE %s LIMIT %s",
                     (like, like, limit)),
    }
