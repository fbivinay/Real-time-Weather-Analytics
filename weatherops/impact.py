"""From location risk to operational impact: which routes, hubs and
deliveries a storm actually touches.

Every route is sampled into points (5 km linehaul, 1 km last-mile). A point
takes the worst score among the stations whose radius covers it; with none
covering, a distance-weighted blend of the two nearest city references
within 150 km; with none of those, it has no coverage and is left out
rather than counted as calm. Station positions never move, so the covering
set for every point is computed once.
"""
import heapq
import math
import zlib
from datetime import timedelta, timezone

from weatherops import geo
from weatherops.risk import category

IST = timezone(timedelta(hours=5, minutes=30))
IDW_RANGE_KM = 150
HIGH = 50


def active_deliveries(route, observed_at):
    """Deliveries in progress on a route: capacity x time-of-day curve x a
    deterministic +/-10% wobble per route and hour."""
    local = observed_at.astimezone(IST)
    h = local.hour + local.minute / 60
    if route.kind == "lastmile":
        f = max(0.05, math.sin(math.pi * (h - 7) / 14)) if 7 <= h <= 21 else 0.05
    else:
        f = 0.6 + 0.4 * math.cos(math.pi * (h - 2) / 12)   # trucks run overnight
    jitter = (zlib.crc32(f"{route.id}:{local:%Y%m%d%H}".encode()) % 2001) / 10000 - 0.1
    return max(0, round(route.capacity * f * (1 + jitter)))


def reroute(network, blocked, hub_a, hub_b, max_km=None):
    """Shortest corridor path from hub_a to hub_b avoiding blocked route ids,
    as (hub ids, km), or None."""
    dist, queue, prev = {hub_a: 0.0}, [(0.0, hub_a)], {}
    while queue:
        d, hub = heapq.heappop(queue)
        if hub == hub_b:
            break
        if d > dist.get(hub, math.inf):
            continue
        for other, route_id, km in network.corridor_graph[hub]:
            if route_id in blocked:
                continue
            nd = d + km
            if nd < dist.get(other, math.inf):
                dist[other], prev[other] = nd, hub
                heapq.heappush(queue, (nd, other))
    if hub_b not in dist or (max_km is not None and dist[hub_b] > max_km):
        return None
    path = [hub_b]
    while path[-1] != hub_a:
        path.append(prev[path[-1]])
    return path[::-1], round(dist[hub_b], 1)


class ImpactModel:
    def __init__(self, network):
        self.network = network
        refs = network.references
        stations = list(network.stations.values())
        self._points = {}
        for route in network.routes.values():
            plan = []
            for lat, lon in route.samples:
                covering = [s.id for s in stations
                            if abs(s.lat - lat) * 111 <= s.radius_km
                            and geo.haversine_km(lat, lon, s.lat, s.lon) <= s.radius_km]
                nearby = sorted((geo.haversine_km(lat, lon, r.lat, r.lon), r.id) for r in refs)
                fallback = [(r_id, km) for km, r_id in nearby if km <= IDW_RANGE_KM]
                plan.append(((lat, lon), covering, fallback))
            self._points[route.id] = plan

    def _score(self, covering, fallback, scores):
        covered = [scores[s] for s in covering if scores.get(s) is not None]
        if covered:
            return max(covered)
        known = [(scores[r], km) for r, km in fallback if scores.get(r) is not None][:2]
        if not known:
            return None
        weights = [1 / max(km, 1.0) ** 2 for _, km in known]
        return sum(w * s for w, (s, _) in zip(weights, known)) / sum(weights)

    def point_score(self, point, scores):
        lat, lon = point
        stations = self.network.stations.values()
        covering = [s.id for s in stations if geo.haversine_km(lat, lon, s.lat, s.lon) <= s.radius_km]
        nearby = sorted((geo.haversine_km(lat, lon, r.lat, r.lon), r.id) for r in self.network.references)
        return self._score(covering, [(r, km) for km, r in nearby if km <= IDW_RANGE_KM], scores)

    def evaluate(self, station_scores, hub_scores, observed_at):
        routes, kpis = {}, {"routes_affected": {"linehaul": 0, "lastmile": 0},
                            "deliveries_active": 0, "deliveries_at_risk": 0}
        for route_id, plan in self._points.items():
            route = self.network.routes[route_id]
            scored = [(self._score(c, f, station_scores), p) for p, c, f in plan]
            scored = [(s, p) for s, p in scored if s is not None]
            active = active_deliveries(route, observed_at)
            if scored:
                worst, peak = max(scored, key=lambda sp: sp[0])
                worst = round(worst)
                status = category(worst)
                exposure = sum(1 for s, _ in scored if s >= HIGH) / len(scored)
            else:
                worst, peak, status, exposure = None, None, "unknown", 0.0
            affected = status in ("high", "critical")
            region = route.city_id
            if route.kind == "linehaul" and affected:
                region = self.network.region_of_point(*peak)
            routes[route_id] = {
                "status": status, "score": worst, "exposure": round(exposure, 3),
                "active": active, "at_risk": active if affected else 0,
                "region": region, "peak": peak,
            }
            kpis["deliveries_active"] += active
            if affected:
                kpis["routes_affected"][route.kind] += 1
                kpis["deliveries_at_risk"] += active

        hubs = {}
        for hub_id in self.network.hubs:
            score = hub_scores.get(hub_id)
            hubs[hub_id] = {"status": "unknown" if score is None else category(score), "score": score}
        kpis["hubs_affected"] = sum(1 for h in hubs.values() if h["status"] in ("high", "critical"))
        kpis["locations_high"] = sum(1 for s in station_scores.values() if s is not None and s >= HIGH)
        return {"routes": routes, "hubs": hubs, "kpis": kpis}
