"""The logistics network WeatherOps protects: real Indian cities and hub
locations, simulated company sensors, and routes generated from a fixed seed
so every service and the dashboard agree on the same geometry.

Linehaul corridors are straight segments between real waypoint cities, not
road-accurate polylines. Good enough to say which corridor a storm crosses;
snap to a road network if distances ever need to be exact.
"""
import json
import random
import sys
from dataclasses import dataclass

from weatherops import geo


@dataclass(frozen=True)
class City:
    id: str
    name: str
    state: str
    lat: float
    lon: float


@dataclass(frozen=True)
class Hub:
    id: str
    name: str
    city_id: str
    lat: float
    lon: float


@dataclass(frozen=True)
class Station:
    id: str
    kind: str  # "reference" | "sensor"
    city_id: str
    hub_id: str | None
    lat: float
    lon: float
    radius_km: float
    name: str = ""


@dataclass(frozen=True)
class Route:
    id: str
    kind: str  # "linehaul" | "lastmile"
    name: str
    hub_ids: tuple
    city_id: str
    points: tuple
    samples: tuple
    length_km: float
    capacity: int


CITIES = tuple(City(*c) for c in [
    ("DEL", "Delhi", "Delhi", 28.6139, 77.2090),
    ("JAI", "Jaipur", "Rajasthan", 26.9124, 75.7873),
    ("JOD", "Jodhpur", "Rajasthan", 26.2389, 73.0243),
    ("LKO", "Lucknow", "Uttar Pradesh", 26.8467, 80.9462),
    ("AGR", "Agra", "Uttar Pradesh", 27.1767, 78.0081),
    ("VNS", "Varanasi", "Uttar Pradesh", 25.3176, 82.9739),
    ("CHD", "Chandigarh", "Chandigarh", 30.7333, 76.7794),
    ("LDH", "Ludhiana", "Punjab", 30.9010, 75.8573),
    ("DDN", "Dehradun", "Uttarakhand", 30.3165, 78.0322),
    ("AMD", "Ahmedabad", "Gujarat", 23.0225, 72.5714),
    ("SRT", "Surat", "Gujarat", 21.1702, 72.8311),
    ("BRD", "Vadodara", "Gujarat", 22.3072, 73.1812),
    ("RJK", "Rajkot", "Gujarat", 22.3039, 70.8022),
    ("MUM", "Mumbai", "Maharashtra", 19.0760, 72.8777),
    ("PUN", "Pune", "Maharashtra", 18.5204, 73.8567),
    ("NSK", "Nashik", "Maharashtra", 19.9975, 73.7898),
    ("NAG", "Nagpur", "Maharashtra", 21.1458, 79.0882),
    ("IDR", "Indore", "Madhya Pradesh", 22.7196, 75.8577),
    ("BPL", "Bhopal", "Madhya Pradesh", 23.2599, 77.4126),
    ("GOA", "Panaji", "Goa", 15.4909, 73.8278),
    ("BLR", "Bengaluru", "Karnataka", 12.9716, 77.5946),
    ("MYS", "Mysuru", "Karnataka", 12.2958, 76.6394),
    ("MLR", "Mangaluru", "Karnataka", 12.9141, 74.8560),
    ("HBL", "Hubballi", "Karnataka", 15.3647, 75.1240),
    ("CHE", "Chennai", "Tamil Nadu", 13.0827, 80.2707),
    ("CBE", "Coimbatore", "Tamil Nadu", 11.0168, 76.9558),
    ("MDU", "Madurai", "Tamil Nadu", 9.9252, 78.1198),
    ("KOC", "Kochi", "Kerala", 9.9312, 76.2673),
    ("TVM", "Thiruvananthapuram", "Kerala", 8.5241, 76.9366),
    ("HYD", "Hyderabad", "Telangana", 17.3850, 78.4867),
    ("VJA", "Vijayawada", "Andhra Pradesh", 16.5062, 80.6480),
    ("VSK", "Visakhapatnam", "Andhra Pradesh", 17.6868, 83.2185),
    ("NLR", "Nellore", "Andhra Pradesh", 14.4426, 79.9865),
    ("KOL", "Kolkata", "West Bengal", 22.5726, 88.3639),
    ("SLG", "Siliguri", "West Bengal", 26.7271, 88.3953),
    ("BBS", "Bhubaneswar", "Odisha", 20.2961, 85.8245),
    ("PAT", "Patna", "Bihar", 25.5941, 85.1376),
    ("RAN", "Ranchi", "Jharkhand", 23.3441, 85.3096),
    ("RPR", "Raipur", "Chhattisgarh", 21.2514, 81.6296),
    ("GUW", "Guwahati", "Assam", 26.1445, 91.7362),
])

# One hub per city, at the logistics cluster that actually serves it rather
# than the city centre. The hub id is its city id.
HUBS = tuple(Hub(c, name, c, lat, lon) for c, name, lat, lon in [
    ("DEL", "Bilaspur (Gurugram)", 28.3020, 76.8890),
    ("MUM", "Bhiwandi", 19.2813, 73.0483),
    ("BLR", "Hoskote", 13.0707, 77.7982),
    ("CHE", "Sriperumbudur", 12.9675, 79.9419),
    ("HYD", "Shamshabad", 17.2543, 78.4290),
    ("KOL", "Dankuni", 22.6800, 88.2900),
    ("AMD", "Changodar", 22.9300, 72.4400),
    ("PUN", "Chakan", 18.7600, 73.8600),
    ("JAI", "Sitapura", 26.7800, 75.8300),
    ("LKO", "Chinhat", 26.8800, 81.0500),
    ("NAG", "MIHAN", 21.0900, 79.0400),
    ("IDR", "Pithampur", 22.6100, 75.6800),
    ("PAT", "Fatuha", 25.5100, 85.3100),
    ("BBS", "Khurda", 20.1800, 85.6200),
    ("GUW", "Amingaon", 26.1900, 91.6700),
    ("KOC", "Kalamassery", 10.0500, 76.3200),
    ("CBE", "Sulur", 11.0300, 77.1300),
    ("VSK", "Anakapalli", 17.6900, 83.0000),
    ("VJA", "Gannavaram", 16.5400, 80.8000),
    ("CHD", "Zirakpur", 30.6400, 76.8200),
    ("SRT", "Kamrej", 21.2700, 72.9600),
    ("RPR", "Urla", 21.3200, 81.6000),
    ("RAN", "Tupudana", 23.2800, 85.3300),
    ("BPL", "Mandideep", 23.1000, 77.5300),
    ("VNS", "Raja Talab", 25.2800, 82.8500),
])

# (hub_a, hub_b, waypoint cities for shape, highway label)
CORRIDORS = (
    ("DEL", "JAI", (), "NH48"),
    ("JAI", "AMD", (), "NH48"),
    ("AMD", "SRT", ("BRD",), "NH48"),
    ("SRT", "MUM", (), "NH48"),
    ("MUM", "PUN", (), "Expressway"),
    ("PUN", "BLR", ("HBL",), "NH48"),
    ("BLR", "CHE", (), "NH48"),
    ("BLR", "HYD", (), "NH44"),
    ("HYD", "NAG", (), "NH44"),
    ("NAG", "BPL", (), ""),
    ("BPL", "DEL", ("AGR",), ""),
    ("CHE", "VJA", ("NLR",), "NH16"),
    ("VJA", "VSK", (), "NH16"),
    ("VSK", "BBS", (), "NH16"),
    ("BBS", "KOL", (), "NH16"),
    ("KOL", "PAT", (), ""),
    ("PAT", "VNS", (), ""),
    ("VNS", "LKO", (), ""),
    ("LKO", "DEL", ("AGR",), ""),
    ("KOL", "GUW", ("SLG",), ""),
    ("DEL", "CHD", (), ""),
    ("BLR", "CBE", (), ""),
    ("CBE", "KOC", (), ""),
    ("CHE", "CBE", (), ""),
    ("HYD", "VJA", (), ""),
    ("MUM", "NAG", ("NSK",), ""),
    ("MUM", "IDR", ("NSK",), ""),
    ("IDR", "BPL", (), ""),
    ("RPR", "NAG", (), ""),
    ("RPR", "BBS", (), ""),
    ("RAN", "KOL", (), ""),
    ("RAN", "PAT", (), ""),
    ("AMD", "IDR", ("BRD",), ""),
)

REFERENCE_RADIUS_KM = 40
SENSOR_RADIUS_KM = 10
LASTMILE_SPOKES = 8


class Network:
    def __init__(self, cities, hubs, stations, routes):
        self.cities = {c.id: c for c in cities}
        self.hubs = {h.id: h for h in hubs}
        self.stations = {s.id: s for s in stations}
        self.routes = {r.id: r for r in routes}
        self.references = [s for s in stations if s.kind == "reference"]
        self.sensors = [s for s in stations if s.kind == "sensor"]
        self.sensors_by_hub = {h.id: [] for h in hubs}
        for s in self.sensors:
            self.sensors_by_hub[s.hub_id].append(s)
        self.hub_by_city = {h.city_id: h for h in hubs}
        self.corridor_graph = {h.id: [] for h in hubs}
        for r in routes:
            if r.kind == "linehaul":
                a, b = r.hub_ids
                self.corridor_graph[a].append((b, r.id, r.length_km))
                self.corridor_graph[b].append((a, r.id, r.length_km))

    def region_of_point(self, lat, lon):
        return min(self.cities.values(), key=lambda c: geo.haversine_km(lat, lon, c.lat, c.lon)).id


def build_network(seed=7):
    rand = random.Random(seed)
    cities = {c.id: c for c in CITIES}

    stations = [
        Station(f"REF-{c.id}", "reference", c.id, None, c.lat, c.lon, REFERENCE_RADIUS_KM, c.name)
        for c in CITIES
    ]
    for hub in HUBS:
        n = rand.choice((4, 5))
        for k in range(n):
            bearing = k * 360 / n + rand.uniform(-20, 20)
            lat, lon = geo.destination(hub.lat, hub.lon, bearing, rand.uniform(2, 12))
            stations.append(Station(
                f"{hub.id}-S{k + 1}", "sensor", hub.city_id, hub.id,
                round(lat, 5), round(lon, 5), SENSOR_RADIUS_KM, f"{hub.name} sensor {k + 1}",
            ))

    routes = []
    for hub in HUBS:
        for k in range(LASTMILE_SPOKES):
            end = geo.destination(hub.lat, hub.lon, k * 45 + rand.uniform(-10, 10), rand.uniform(6, 15))
            points = ((hub.lat, hub.lon), (round(end[0], 5), round(end[1], 5)))
            routes.append(Route(
                f"LM-{hub.id}-{k + 1}", "lastmile", f"{hub.name} last-mile {k + 1}",
                (hub.id,), hub.city_id, points, tuple(geo.sample_polyline(points, 1)),
                round(geo.polyline_length_km(points), 2), rand.randint(20, 45),
            ))

    hubs = {h.id: h for h in HUBS}
    for a, b, waypoints, highway in CORRIDORS:
        ha, hb = hubs[a], hubs[b]
        points = ((ha.lat, ha.lon), *((cities[w].lat, cities[w].lon) for w in waypoints), (hb.lat, hb.lon))
        name = f"{cities[a].name}–{cities[b].name}"
        mid = points[len(points) // 2] if len(points) > 2 else (
            (ha.lat + hb.lat) / 2, (ha.lon + hb.lon) / 2)
        region = min(CITIES, key=lambda c: geo.haversine_km(*mid, c.lat, c.lon)).id
        routes.append(Route(
            f"LH-{a}-{b}", "linehaul", f"{highway} {name}".strip(),
            (a, b), region, points, tuple(geo.sample_polyline(points, 5)),
            round(geo.polyline_length_km(points), 1), rand.randint(4, 12),
        ))

    return Network(CITIES, HUBS, stations, routes)


NETWORK = build_network()


def to_geojson(network):
    def point(lat, lon):
        return {"type": "Point", "coordinates": [lon, lat]}

    features = []
    for h in network.hubs.values():
        features.append({"type": "Feature", "geometry": point(h.lat, h.lon), "properties": {
            "layer": "hub", "id": h.id, "name": h.name, "city_id": h.city_id,
            "city": network.cities[h.city_id].name}})
    for s in network.stations.values():
        features.append({"type": "Feature", "geometry": point(s.lat, s.lon), "properties": {
            "layer": "station", "id": s.id, "kind": s.kind, "name": s.name,
            "city_id": s.city_id, "hub_id": s.hub_id, "radius_km": s.radius_km}})
    for r in network.routes.values():
        features.append({"type": "Feature", "geometry": {
            "type": "LineString", "coordinates": [[lon, lat] for lat, lon in r.points]},
            "properties": {"layer": "route", "id": r.id, "kind": r.kind, "name": r.name,
                           "city_id": r.city_id, "capacity": r.capacity,
                           "length_km": r.length_km}})
    return {"type": "FeatureCollection", "features": features}


def _round_coords(obj, ndigits=4):
    if isinstance(obj, float):
        return round(obj, ndigits)
    if isinstance(obj, list):
        return [_round_coords(v, ndigits) for v in obj]
    if isinstance(obj, dict):
        return {k: _round_coords(v, ndigits) for k, v in obj.items()}
    return obj


if __name__ == "__main__":
    json.dump(_round_coords(to_geojson(NETWORK)), sys.stdout, separators=(",", ":"))
