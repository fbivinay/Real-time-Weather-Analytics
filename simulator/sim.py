"""ShopFlow operations simulator: turns simulated time into a stream of
business events. Pure (no I/O) - main.py owns the clock, Kafka and Redis.

Orders arrive at the company's demand rate. Each is planned onto a dispatch
wave (every 2 h) 2-6 h out, or onto a delivery slot up to two days ahead.
Orders sharing a route and wave ride one truck: the trip is assigned 30 min
before the wave, dispatched, reports movement every 30 min, meets the
replayed real rain along its route (the truth that sets its delay), reaches
the delivery hub and completes orders over the last mile.
"""
import heapq
import random
from datetime import timedelta

from weatherops import company as co
from weatherops import delay, events
from weatherops.rainfall import hourly_class

WAVE_H = 2
MOVE_EVERY = timedelta(minutes=30)
DELAY_REPORT_MIN = 15
IST = timedelta(hours=5, minutes=30)
SIGMA = 0.3


def _wave(t):
    """First dispatch wave at or after t (waves every WAVE_H hours, on the hour UTC)."""
    base = t.replace(minute=0, second=0, microsecond=0)
    if base < t:
        base += timedelta(hours=1)
    while base.hour % WAVE_H:
        base += timedelta(hours=1)
    return base


class Simulator:
    def __init__(self, hourly, start, seed=0, dup_rate=0.003, invalid_rate=0.001, rate_scale=1.0):
        self.rng = random.Random(seed)
        self.hourly = hourly
        self.t = start
        self.rate_scale = rate_scale
        self.dup_rate = dup_rate
        self.invalid_rate = invalid_rate
        self.queue = []          # (sim_time, seq, handler, arg)
        self.seq = 0
        self.trips = {}          # (route_id, dispatch) -> trip
        self.order_seq = 0
        self.carry = 0.0         # fractional orders carried between steps
        self.weather_hour = None
        net = co.NETWORK
        self.cities, self.city_w = zip(*co.DEMAND.items())
        self.customers = {}
        for c in co.customers():
            self.customers.setdefault(c["city_id"], []).append(c["id"])
        self.products = {}
        for p in co.products():
            self.products.setdefault(p["category_id"], []).append(p)
        self.trucks = {w: [v["id"] for v in co.vehicles() if v["base_id"] == w] for w in net.warehouses}

    # ---- scheduling -------------------------------------------------------
    def _at(self, when, handler, arg):
        self.seq += 1
        heapq.heappush(self.queue, (when, self.seq, handler, arg))

    def step(self, now):
        out = []
        out += self._weather(now)
        out += self._new_orders(now)
        while self.queue and self.queue[0][0] <= now:
            when, _, handler, arg = heapq.heappop(self.queue)
            out += handler(when, arg)
        self.t = now
        return self._faults(out)

    # ---- weather ----------------------------------------------------------
    def _weather(self, now):
        hour = now.replace(minute=0, second=0, microsecond=0)
        if hour == self.weather_hour:
            return []
        self.weather_hour = hour
        out = []
        for city_id in co.NETWORK.cities:
            mm = self.hourly.mmph(city_id, hour)
            out.append(events.make("WEATHER_EVENT", hour, {"rain_mmph": mm, "severity": hourly_class(mm)},
                                   city_id=city_id))
        return out

    # ---- demand -----------------------------------------------------------
    def _new_orders(self, now):
        minutes = (now - self.t).total_seconds() / 60
        ist_hour = (now + IST).hour
        rate = co.national_orders(now.date()) / 1440 * co.hourly_share(ist_hour) * self.rate_scale
        expected = rate * minutes + self.carry
        n = int(expected)
        self.carry = expected - n
        return [e for _ in range(n) for e in self._create(now)]

    def _create(self, now):
        rng = self.rng
        city_id = rng.choices(self.cities, self.city_w)[0]
        primary, secondary = co.NETWORK.routes_to[city_id]
        route = primary if rng.random() < primary.share else secondary
        cat = rng.choices(co.CATEGORIES, [c[2] for c in co.CATEGORIES])[0]
        tier = rng.choices(co.TIERS, co.TIER_MIX)[0]
        items = rng.sample(self.products[cat[0]], rng.choice((1, 1, 1, 2, 2, 3)))
        lines = [{"product_id": p["id"], "qty": 1, "price_inr": p["price"]} for p in items]
        lead = rng.uniform(2, 6) if rng.random() < 0.62 else rng.uniform(6, 46)
        dispatch = _wave(now + timedelta(hours=lead))
        promised = dispatch + timedelta(hours=co.promised_h(route, tier))
        self.order_seq += 1
        order_id = f"ORD-{self.order_seq:07d}"
        order = {"id": order_id, "tier": tier, "category_id": cat[0], "promised": promised,
                 "value_inr": round(sum(line["price_inr"] for line in lines))}
        key = (route.id, dispatch)
        trip = self.trips.get(key)
        if trip is None:
            trip = self.trips[key] = {"id": f"TRP-{route.city_id}-{len(self.trips) + 1:06d}", "route": route,
                                      "dispatch": dispatch, "orders": []}
            self._at(dispatch - timedelta(minutes=30), self._assign, key)
            self._at(dispatch, self._dispatch, key)
        trip["orders"].append(order)
        return [events.make("ORDER_CREATED", now, {
            "customer_id": rng.choice(self.customers[city_id]), "warehouse_id": route.warehouse_id,
            "hub_id": route.hub_id, "route_code": route.code, "category_id": cat[0], "tier": tier,
            "value_inr": order["value_inr"], "items": lines, "planned_dispatch": events.fmt_ts(dispatch),
            "promised_at": events.fmt_ts(promised),
        }, order_id=order_id, city_id=city_id, route_id=route.id)]

    # ---- trip lifecycle ---------------------------------------------------
    def _assign(self, when, key):
        trip = self.trips[key]
        route = trip["route"]
        trip["vehicle"] = self.rng.choice(self.trucks[route.warehouse_id])
        return [events.make("ORDER_ASSIGNED", when, {"trip_id": trip["id"], "vehicle_id": trip["vehicle"]},
                            order_id=o["id"], city_id=route.city_id, route_id=route.id)
                for o in trip["orders"]]

    def _dispatch(self, when, key):
        trip = self.trips.pop(key)
        route = trip["route"]
        cls = delay.actual_class(route, when, self.hourly)
        noise = self.rng.lognormvariate(-SIGMA ** 2 / 2, SIGMA)
        weather_h = delay.weather_delay_h(route, cls) * noise
        other_h = self.rng.expovariate(1 / 0.35)
        normal = co.normal_eta_h(route)
        factor = (normal + weather_h + other_h) / normal
        linehaul_h = (co.WAREHOUSE_DWELL_H + route.distance_km / co.LINEHAUL_KMPH + co.HUB_DWELL_H) * factor
        trip.update(cls=cls, weather_h=weather_h, factor=factor, dispatched_at=when,
                    arrive=when + timedelta(hours=linehaul_h), vehicle=trip.get("vehicle") or "unassigned")
        out = [events.make("VEHICLE_DISPATCHED", when, {
            "trip_id": trip["id"], "vehicle_id": trip["vehicle"], "warehouse_id": route.warehouse_id,
            "hub_id": route.hub_id, "orders": len(trip["orders"]),
            "planned_arrival": events.fmt_ts(when + timedelta(hours=linehaul_h / factor)),
        }, city_id=route.city_id, route_id=route.id)]
        if weather_h * 60 >= DELAY_REPORT_MIN:
            self._at(when + timedelta(hours=linehaul_h * 0.4), self._delay, trip)
        self._at(when + MOVE_EVERY, self._move, trip)
        self._at(trip["arrive"], self._arrive, trip)
        return out

    def _move(self, when, trip):
        if when >= trip["arrive"]:
            return []
        route = trip["route"]
        span = (trip["arrive"] - trip["dispatched_at"]).total_seconds()
        p = max(0.0, min(1.0, (when - trip["dispatched_at"]).total_seconds() / max(span, 1)))
        wh = co.NETWORK.warehouses[route.warehouse_id]
        hub = co.NETWORK.hubs[route.hub_id]
        self._at(when + MOVE_EVERY, self._move, trip)
        return [events.make("VEHICLE_MOVEMENT", when, {
            "trip_id": trip["id"], "vehicle_id": trip["vehicle"], "progress": round(p, 3),
            "lat": round(wh.lat + (hub.lat - wh.lat) * p, 4), "lon": round(wh.lon + (hub.lon - wh.lon) * p, 4),
            "near_city": delay.city_at(route, p),
        }, city_id=route.city_id, route_id=route.id)]

    def _delay(self, when, trip):
        route = trip["route"]
        return [events.make("DELIVERY_DELAY", when, {
            "trip_id": trip["id"], "vehicle_id": trip["vehicle"], "delay_min": round(trip["weather_h"] * 60),
            "cause": trip["cls"], "orders": len(trip["orders"]),
        }, city_id=route.city_id, route_id=route.id)]

    def _arrive(self, when, trip):
        route = trip["route"]
        for o in trip["orders"]:
            last = co.LAST_MILE_H * trip["factor"] * self.rng.uniform(0.3, 1.7)
            self._at(when + timedelta(hours=last), self._complete, (trip, o))
        return [events.make("HUB_ARRIVAL", when, {
            "trip_id": trip["id"], "vehicle_id": trip["vehicle"], "hub_id": route.hub_id,
            "orders": len(trip["orders"]), "weather_class": trip["cls"],
        }, city_id=route.city_id, route_id=route.id)]

    def _complete(self, when, arg):
        trip, o = arg
        route = trip["route"]
        normal = co.normal_eta_h(route)
        total_delay_min = max(0.0, ((when - trip["dispatch"]).total_seconds() / 3600 - normal) * 60)
        return [events.make("DELIVERY_COMPLETED", when, {
            "trip_id": trip["id"], "route_code": route.code, "tier": o["tier"], "category_id": o["category_id"],
            "value_inr": o["value_inr"], "weather_class": trip["cls"],
            "weather_delay_min": round(trip["weather_h"] * 60, 1), "delay_min": round(total_delay_min, 1),
            "sla_breached": when > o["promised"], "dispatched_at": events.fmt_ts(trip["dispatch"]),
        }, order_id=o["id"], city_id=route.city_id, route_id=route.id)]

    # ---- data-quality faults (Spark must catch them) ----------------------
    def _faults(self, out):
        extra = []
        for e in out:
            r = self.rng.random()
            if r < self.dup_rate:
                extra.append(dict(e))
            elif r < self.dup_rate + self.invalid_rate:
                bad = dict(e)
                bad[self.rng.choice(("type", "sim_time"))] = self.rng.choice(("ORDER_EXPLODED", None))
                bad["event_id"] = e["event_id"] + "x"
                extra.append(bad)
        return out + extra

