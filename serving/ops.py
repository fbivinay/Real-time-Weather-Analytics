"""Live operations state of ShopFlow, built from the clean event stream.

Pure (no I/O): engine.py feeds events in, calls the compute_* functions on
its tick and writes the results to Redis and Postgres. Everything a manager
sees about "now" and "next 48 hours" is computed here.
"""
import json
from collections import Counter, defaultdict
from datetime import timedelta

from weatherops import company as co
from weatherops import delay, risk
from weatherops.events import parse_ts
from weatherops.rainfall import CLASSES, forecast

NET = co.NETWORK
ACTIVE = ("scheduled", "assigned", "in_transit", "at_hub")
WINDOWS = (6, 12, 24, 48)
RANK = {c: i for i, c in enumerate(CLASSES)}
KEEP_DELIVERED = timedelta(days=2)
EXPIRE_AFTER = timedelta(hours=6)


def _day(t):
    return t.date().isoformat()


class Ops:
    def __init__(self, hourly, coefs, route_hist=None):
        self.hourly = hourly
        self.coefs = coefs
        self.route_hist = route_hist or {}
        self.reset()

    def reset(self):
        self.orders = {}
        self.trips = {}
        self.weather = {c: {"rain_mmph": 0.0, "severity": "none"} for c in NET.cities}
        self.sim_now = None
        self.dirty = set()            # order ids to upsert
        self.new_items = []           # (order_id, product_id, qty, price)
        self.rollup = defaultdict(Counter)   # (date, route, cat) -> counters
        self.rollup_sev = {}          # (date, route, cat) -> worst class
        self.events_seen = 0
        self.type_counts = Counter()
        self.last_event = None
        self.cohort_risk = {}         # (route, tier, dispatch) -> assessment
        self.resets = getattr(self, "resets", 0)

    # ---- events -----------------------------------------------------------
    def apply(self, e):
        """Returns True when the replay wrapped (simulated time jumped back)."""
        t = parse_ts(e["sim_time"])
        wrapped = self.sim_now is not None and t < self.sim_now - timedelta(hours=12)
        if wrapped:
            self.resets += 1
            self.reset()
        if self.sim_now is None or t > self.sim_now:
            self.sim_now = t
        self.events_seen += 1
        self.type_counts[e["type"]] += 1
        self.last_event = {"type": e["type"], "sim_time": e["sim_time"], "emitted_at": e.get("emitted_at"),
                           "order_id": e.get("order_id"), "city_id": e.get("city_id"), "route_id": e.get("route_id")}
        handler = getattr(self, "_" + e["type"].lower(), None)
        if handler:
            p = e.get("payload") or "{}"
            handler(e, t, json.loads(p) if isinstance(p, str) else p)
        return wrapped

    def _weather_event(self, e, t, p):
        self.weather[e["city_id"]] = {"rain_mmph": p["rain_mmph"], "severity": p["severity"], "hour": e["sim_time"]}

    def _order_created(self, e, t, p):
        oid = e["order_id"]
        self.orders[oid] = {
            "id": oid, "customer_id": p["customer_id"], "city_id": e["city_id"], "route_id": e["route_id"],
            "warehouse_id": p["warehouse_id"], "hub_id": p["hub_id"], "category_id": p["category_id"],
            "tier": p["tier"], "value_inr": p["value_inr"], "items": len(p["items"]), "status": "scheduled",
            "created_at": t, "planned_dispatch": parse_ts(p["planned_dispatch"]),
            "promised_at": parse_ts(p["promised_at"]), "dispatched_at": None, "eta_at": None,
            "delivered_at": None, "vehicle_id": None, "trip_id": None, "weather_class": None,
            "delay_min": None, "sla_breached": None,
        }
        self.new_items += [(oid, i["product_id"], i["qty"], i["price_inr"]) for i in p["items"]]
        self.dirty.add(oid)

    def _order_assigned(self, e, t, p):
        o = self.orders.get(e["order_id"])
        if o:
            o.update(status="assigned", vehicle_id=p["vehicle_id"], trip_id=p["trip_id"])
            self.trips.setdefault(p["trip_id"], {"orders": set(), "route_id": e["route_id"]})["orders"].add(o["id"])
            self.dirty.add(o["id"])

    def _vehicle_dispatched(self, e, t, p):
        trip = self.trips.setdefault(p["trip_id"], {"orders": set(), "route_id": e["route_id"]})
        trip.update(dispatched=t, vehicle_id=p["vehicle_id"], planned_arrival=parse_ts(p["planned_arrival"]),
                    delay_min=0, cause="none", progress=0.0, status="in_transit", near_city=None)
        route = NET.routes[e["route_id"]]
        for oid in trip["orders"]:
            o = self.orders.get(oid)
            if o:
                o.update(status="in_transit", dispatched_at=t,
                         eta_at=t + timedelta(hours=co.normal_eta_h(route)))
                self.dirty.add(oid)

    def _vehicle_movement(self, e, t, p):
        trip = self.trips.get(p["trip_id"])
        if trip:
            trip.update(progress=p["progress"], lat=p["lat"], lon=p["lon"], near_city=p["near_city"])

    def _delivery_delay(self, e, t, p):
        trip = self.trips.get(p["trip_id"])
        if trip:
            trip.update(delay_min=p["delay_min"], cause=p["cause"])
            for oid in trip["orders"]:
                o = self.orders.get(oid)
                if o and o["eta_at"]:
                    o["eta_at"] = o["dispatched_at"] + timedelta(
                        hours=co.normal_eta_h(NET.routes[o["route_id"]])) + timedelta(minutes=p["delay_min"])
                    o["weather_class"] = p["cause"]
                    self.dirty.add(oid)

    def _hub_arrival(self, e, t, p):
        trip = self.trips.get(p["trip_id"])
        if trip:
            trip["status"] = "at_hub"
            for oid in trip["orders"]:
                o = self.orders.get(oid)
                if o:
                    o["status"] = "at_hub"
                    self.dirty.add(oid)

    def _delivery_completed(self, e, t, p):
        o = self.orders.get(e["order_id"])
        if not o:
            return
        o.update(status="delivered", delivered_at=t, weather_class=p["weather_class"], delay_min=p["delay_min"],
                 weather_delay_min=p["weather_delay_min"], sla_breached=p["sla_breached"])
        self.dirty.add(o["id"])
        trip = self.trips.get(p["trip_id"])
        if trip:
            trip["orders"].discard(o["id"])
            if not trip["orders"]:
                self.trips.pop(p["trip_id"], None)
        key = (_day(t), o["route_id"], o["category_id"])
        c = self.rollup[key]
        cls = p["weather_class"]
        c["orders"] += 1
        c["exposed"] += cls != "none"
        c["affected"] += p["weather_delay_min"] >= 5
        c["delayed"] += p["delay_min"] >= 30
        c["sla_breaches"] += bool(p["sla_breached"])
        c["delay_min_sum"] += p["weather_delay_min"]
        c["cost_transport"] += p["weather_delay_min"] / 60 * delay.COST_PER_DELAY_HOUR
        c["cost_reship"] += delay.FAILED_RATE[cls] * delay.COST_RESHIP
        c["cost_sla"] += delay.COST_SLA[o["tier"]] if p["sla_breached"] and cls != "none" else 0
        if RANK[cls] >= RANK[self.rollup_sev.get(key, "none")]:
            self.rollup_sev[key] = cls

    # ---- housekeeping -----------------------------------------------------
    def prune(self):
        """Forget delivered orders after two simulated days and scheduled
        orders whose dispatch passed long ago (lost to a simulator restart)."""
        if not self.sim_now:
            return []
        gone = []
        for oid, o in list(self.orders.items()):
            old_done = o["status"] == "delivered" and o["delivered_at"] < self.sim_now - KEEP_DELIVERED
            lost = o["status"] in ("scheduled", "assigned") and o["planned_dispatch"] < self.sim_now - EXPIRE_AFTER
            if old_done or lost:
                gone.append(oid)
                del self.orders[oid]
        return gone

    def take_dirty(self):
        rows = [self.orders[oid] for oid in self.dirty if oid in self.orders]
        items = [i for i in self.new_items if i[0] in self.orders]
        self.new_items = []
        self.dirty = set()
        return rows, items

    def take_rollup(self):
        out = [(k, dict(v), self.rollup_sev.get(k, "none")) for k, v in self.rollup.items()]
        self.rollup = defaultdict(Counter)
        return out

    # ---- forecast and risk ------------------------------------------------
    def forecast_fn(self):
        cache = {}
        now = self.sim_now

        def fn(city_id, when, lead_h):
            key = (city_id, when.replace(minute=0, second=0, microsecond=0))
            if key not in cache:
                cache[key] = forecast(self.hourly, city_id, key[1], max(0.0, (key[1] - now).total_seconds() / 3600))
            return cache[key]
        return fn

    def hist(self, route_id):
        h = self.route_hist.get(route_id)
        if h:
            return h
        r = NET.routes[route_id]
        return {c: {"delay_ratio": (delay.MULTIPLIER[c] - 1) * r.sensitivity, "on_time": 0.95} for c in CLASSES}

    def cohorts(self, horizon_h=48):
        end = self.sim_now + timedelta(hours=horizon_h)
        groups = Counter()
        for o in self.orders.values():
            if o["status"] in ("scheduled", "assigned") and self.sim_now <= o["planned_dispatch"] <= end:
                groups[(o["route_id"], o["tier"], o["planned_dispatch"])] += 1
        return groups

    def compute_risk(self):
        """Risk for every upcoming-order cohort (route x tier x dispatch wave)."""
        fc = self.forecast_fn()
        groups = self.cohorts()
        exposures = {}
        wh_total, wh_exposed = Counter(), Counter()
        for (route_id, tier, dispatch), n in groups.items():
            route = NET.routes[route_id]
            key = (route_id, dispatch)
            if key not in exposures:
                exposures[key] = delay.exposure(route, dispatch, self.sim_now, fc)
            wh_total[route.warehouse_id] += n
            wh_exposed[route.warehouse_id] += n if exposures[key]["exposed_share"] > 0 else 0
        out = {}
        for (route_id, tier, dispatch), n in groups.items():
            route = NET.routes[route_id]
            share = wh_exposed[route.warehouse_id] / max(1, wh_total[route.warehouse_id])
            a = risk.assess(route, tier, dispatch, exposures[(route_id, dispatch)], self.hist(route_id), share,
                            self.coefs)
            a["orders"] = n
            out[(route_id, tier, dispatch)] = a
        self.cohort_risk = out
        return out

    # ---- views ------------------------------------------------------------
    def order_risk(self, o):
        return self.cohort_risk.get((o["route_id"], o["tier"], o["planned_dispatch"]))

    def future_summary(self):
        out = {}
        for w in WINDOWS:
            end = self.sim_now + timedelta(hours=w)
            rows = [(k, a) for k, a in self.cohort_risk.items() if k[2] <= end]
            n = sum(a["orders"] for _, a in rows)
            out[str(w)] = {
                "scheduled": n,
                "exposed": sum(a["orders"] for _, a in rows if a["p_rain"] >= delay.EXPOSED_P_RAIN),
                "heavy_exposed": sum(a["orders"] for _, a in rows if a["expected_class"] in ("heavy", "extreme")),
                "high": sum(a["orders"] for _, a in rows if a["category"] == "High"),
                "critical": sum(a["orders"] for _, a in rows if a["category"] == "Critical"),
                "expected_delay_min": round(sum(a["expected_delay_min"] * a["orders"] for _, a in rows) / max(1, n), 1),
                "sla_breaches": round(sum(a["p_breach"] * a["orders"] for _, a in rows)),
            }
        return out

    def timeline(self, hours=48):
        buckets = []
        fc = self.forecast_fn()
        total_demand = sum(co.DEMAND.values())
        for h in range(hours):
            start = self.sim_now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=h)
            rows = [a for k, a in self.cohort_risk.items() if start <= k[2] < start + timedelta(hours=1)]
            n = sum(a["orders"] for a in rows)
            p_rain = sum(fc(c, start, h)["p_rain"] * co.DEMAND[c] for c in NET.cities) / total_demand
            buckets.append({
                "hour": start.isoformat().replace("+00:00", "Z"), "orders": n,
                "avg_score": round(sum(a["score"] * a["orders"] for a in rows) / n, 1) if n else 0,
                "high_plus": sum(a["orders"] for a in rows if a["category"] in ("High", "Critical")),
                "sla_breaches": round(sum(a["p_breach"] * a["orders"] for a in rows)),
                "rain_probability": round(p_rain, 3),
            })
        return buckets

    def live(self):
        """Present tense: in-transit exposure, delays, today's totals."""
        today = self.sim_now.date()
        active = [o for o in self.orders.values() if o["status"] in ("in_transit", "at_hub")]
        delivered_today = [o for o in self.orders.values()
                           if o["status"] == "delivered" and o["delivered_at"].date() == today]
        created_today = sum(1 for o in self.orders.values() if o["created_at"].date() == today)
        wet_city = {c for c, w in self.weather.items() if w["severity"] != "none"}

        def exposed(o):
            trip = self.trips.get(o["trip_id"]) or {}
            near = trip.get("near_city") or NET.warehouses[o["warehouse_id"]].city_id
            return o["city_id"] in wet_city or near in wet_city

        exposed_active = [o for o in active if exposed(o)]
        affected = [o for o in active if (self.trips.get(o["trip_id"]) or {}).get("delay_min", 0) >= 5]
        late_active = [o for o in active if o["eta_at"] and o["eta_at"] > o["promised_at"]]
        weather_cost = sum(o.get("weather_delay_min", 0) / 60 * delay.COST_PER_DELAY_HOUR
                           + (delay.COST_SLA[o["tier"]] if o["sla_breached"] and o["weather_class"] != "none" else 0)
                           for o in delivered_today)
        weather_cost += sum((self.trips.get(o["trip_id"]) or {}).get("delay_min", 0) / 60 * delay.COST_PER_DELAY_HOUR
                            for o in affected)
        delays = [o["delay_min"] for o in delivered_today]
        sla_risk = sum(a["orders"] for k, a in self.cohort_risk.items()
                       if k[2] <= self.sim_now + timedelta(hours=24) and a["p_breach"] >= 0.5)
        return {
            "total_orders": created_today,
            "in_transit": len(active),
            "weather_exposed": len(exposed_active),
            "weather_affected": len(affected) + sum(1 for o in delivered_today if o.get("weather_delay_min", 0) >= 5),
            "delayed": len(late_active) + sum(1 for o in delivered_today if o["delay_min"] >= 30),
            "sla_risk": sla_risk + len(late_active),
            "avg_delay_min": round(sum(delays) / len(delays), 1) if delays else 0.0,
            "weather_cost_inr": round(weather_cost),
            "delivered_today": len(delivered_today),
            "on_time_rate": round(1 - sum(bool(o["sla_breached"]) for o in delivered_today) / max(1, len(delivered_today)), 4),
            "active_trips": sum(1 for t in self.trips.values() if t.get("status") == "in_transit"),
            "delayed_trips": sum(1 for t in self.trips.values() if t.get("status") == "in_transit" and t.get("delay_min", 0) >= 15),
            "wet_cities": sorted(wet_city),
        }

    def impact(self):
        """Impact 0-100 per route, city, state, warehouse and hub: how much the
        next 12 h of deliveries and the trucks on the road now are hit by rain."""
        horizon = self.sim_now + timedelta(hours=12)
        route = defaultdict(lambda: {"orders": 0, "exposed": 0, "score_sum": 0.0, "delay_sum": 0.0,
                                     "breaches": 0.0, "in_transit": 0, "delayed_trips": 0})
        for (route_id, tier, dispatch), a in self.cohort_risk.items():
            if dispatch > horizon:
                continue
            r = route[route_id]
            r["orders"] += a["orders"]
            r["exposed"] += a["orders"] if a["p_rain"] >= delay.EXPOSED_P_RAIN else 0
            r["score_sum"] += a["score"] * a["orders"]
            r["delay_sum"] += a["expected_delay_min"] * a["orders"]
            r["breaches"] += a["p_breach"] * a["orders"]
        for t in self.trips.values():
            if t.get("status") == "in_transit":
                r = route[t["route_id"]]
                r["in_transit"] += len(t["orders"])
                r["delayed_trips"] += t.get("delay_min", 0) >= 15
        routes = {}
        for rid, r in route.items():
            n = max(1, r["orders"])
            wet_now = self.weather[NET.routes[rid].city_id]["severity"]
            score = r["score_sum"] / n
            score = min(100.0, score + 10 * min(1, r["delayed_trips"]) + 5 * RANK[wet_now])
            routes[rid] = {"id": rid, "code": NET.routes[rid].code, "score": round(score, 1),
                           "category": risk.category(score), "orders": r["orders"], "exposed": r["exposed"],
                           "avg_delay_min": round(r["delay_sum"] / n, 1), "sla_risk": round(r["breaches"]),
                           "in_transit": r["in_transit"], "delayed_trips": r["delayed_trips"],
                           "rain_now": wet_now}

        def roll(key_fn, ids):
            out = {}
            for gid in ids:
                rs = [routes[r.id] for r in NET.routes.values() if key_fn(r) == gid and r.id in routes]
                n = sum(x["orders"] for x in rs)
                score = sum(x["score"] * x["orders"] for x in rs) / n if n else 0.0
                top = max(rs, key=lambda x: (x["score"], x["orders"]), default=None)
                out[gid] = {"id": gid, "score": round(score, 1), "category": risk.category(score), "orders": n,
                            "exposed": sum(x["exposed"] for x in rs),
                            "potential_delays": sum(x["exposed"] for x in rs if x["avg_delay_min"] >= 30),
                            "avg_delay_min": round(sum(x["avg_delay_min"] * x["orders"] for x in rs) / n, 1) if n else 0,
                            "sla_risk": sum(x["sla_risk"] for x in rs), "in_transit": sum(x["in_transit"] for x in rs),
                            "top_route": top["code"] if top else None}
            return out

        cities = roll(lambda r: r.city_id, NET.cities)
        for cid, c in cities.items():
            c.update(name=NET.cities[cid].name, state=NET.cities[cid].state, **self.weather[cid])
        states = roll(lambda r: NET.cities[r.city_id].state, NET.states)
        warehouses = roll(lambda r: r.warehouse_id, NET.warehouses)
        hubs = roll(lambda r: r.hub_id, NET.hubs)
        return {"routes": routes, "cities": cities, "states": states, "warehouses": warehouses, "hubs": hubs}

    def alerts(self, impact, summary, live):
        out = []
        s12 = summary["12"]
        if s12["heavy_exposed"]:
            out.append({"level": "high", "text": f"{s12['heavy_exposed']:,} deliveries dispatching in the next 12 h "
                                                 "are exposed to heavy rainfall."})
        if live["delayed_trips"]:
            out.append({"level": "high", "text": f"{live['delayed_trips']} trucks on the road are running late "
                                                 "because of rain."})
        top = sorted(impact["routes"].values(), key=lambda r: -r["avg_delay_min"])[:3]
        top = [r for r in top if r["avg_delay_min"] >= 15]
        if top:
            out.append({"level": "medium", "text": "Highest expected delay: " + ", ".join(
                f"{r['code']} (+{round(r['avg_delay_min'])} min)" for r in top) + "."})
        for r in sorted(impact["routes"].values(), key=lambda r: -r["sla_risk"])[:2]:
            if r["sla_risk"] >= 5:
                out.append({"level": "medium", "text": f"{r['sla_risk']} orders on {r['code']} may breach their SLA."})
        total_exposed = sum(w["exposed"] for w in impact["warehouses"].values())
        if total_exposed:
            wid, w = max(impact["warehouses"].items(), key=lambda kv: kv[1]["exposed"])
            share = w["exposed"] / total_exposed
            if share >= 0.25:
                out.append({"level": "info", "text": f"{NET.warehouses[wid].name} handles {round(share * 100)}% of "
                                                     "weather-exposed deliveries in the next 12 h."})
        if not out:
            out.append({"level": "info", "text": "No significant rainfall exposure across the network right now."})
        return out

    def critical_orders(self, limit=20):
        """Highest-risk upcoming orders, one row per cohort (route x service x
        wave): orders in a cohort share every risk input, so the rest are
        counted as `similar` instead of repeating the same row."""
        cohorts = {}
        for o in self.orders.values():
            if o["status"] not in ("scheduled", "assigned"):
                continue
            a = self.order_risk(o)
            if not a or a["category"] not in ("High", "Critical"):
                continue
            key = (o["route_id"], o["tier"], o["planned_dispatch"])
            if key in cohorts:
                cohorts[key][2] += 1
            else:
                cohorts[key] = [o, a, 0]
        rows = sorted(cohorts.values(), key=lambda x: (-x[1]["score"], x[0]["planned_dispatch"]))[:limit]
        return [{**order_view(o, a), "similar": n} for o, a, n in rows]

def iso(t):
    return t.isoformat().replace("+00:00", "Z") if t else None


def order_view(o, a=None):
    r = NET.routes[o["route_id"]]
    out = {"id": o["id"], "route_id": r.id, "route": r.code, "city_id": o["city_id"],
           "city": NET.cities[o["city_id"]].name, "warehouse_id": o["warehouse_id"], "tier": o["tier"],
           "category_id": o["category_id"], "value_inr": o["value_inr"], "status": o["status"],
           "planned_dispatch": iso(o["planned_dispatch"]), "promised_at": iso(o["promised_at"]),
           "eta_at": iso(o["planned_dispatch"] + timedelta(hours=co.normal_eta_h(r)))}
    if a:
        out.update(score=a["score"], category=a["category"], expected_class=a["expected_class"],
                   expected_delay_min=a["expected_delay_min"], p_breach=a["p_breach"], p_rain=a["p_rain"])
    return out
