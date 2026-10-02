"""ShopFlow India - the synthetic e-commerce company (demo data).

Real cities and logistics parks; everything else (warehouse roles, routes,
sensitivities, catalogue, customers, fleet) is generated from fixed seeds so
the seed job, the simulator, the engine and the API agree on one network.

Customer -> Order -> Warehouse -> Hub -> Route -> Vehicle -> Delivery:
an order for a city ships from one of the two nearest warehouses along that
warehouse's route to the city's delivery hub, then goes out for last mile.
"""
import random
from dataclasses import dataclass

from weatherops import geo
from weatherops.network import CITIES, HUBS

COMPANY = "ShopFlow India"
ROAD_FACTOR = 1.25          # road km per great-circle km
LOCAL_KM = 35               # warehouse -> hub inside the same metro
LINEHAUL_KMPH = 50
WAREHOUSE_DWELL_H = 0.5
HUB_DWELL_H = 0.5
LAST_MILE_H = 1.0
CORRIDOR_KM = 45            # a city this close to a route's line shares its weather

# Fulfilment centres at the parks of 10 metros.
WAREHOUSE_CITIES = ("DEL", "MUM", "BLR", "CHE", "HYD", "KOL", "AMD", "PUN", "LKO", "GUW")

# Relative order demand (metro size and e-commerce penetration, rounded).
DEMAND = {
    "DEL": 10, "MUM": 9, "BLR": 9, "HYD": 6, "CHE": 6, "KOL": 6, "PUN": 5, "AMD": 4.5,
    "JAI": 3, "LKO": 3, "SRT": 3, "NAG": 2, "IDR": 2.5, "BPL": 2, "CHD": 2, "PAT": 2,
    "KOC": 2.5, "CBE": 2, "VSK": 2, "VJA": 1.5, "BBS": 1.8, "GUW": 1.8, "TVM": 1.6,
    "MYS": 1.4, "MLR": 1.2, "GOA": 1.0, "HBL": 1.0, "MDU": 1.3, "NLR": 0.8, "RAN": 1.4,
    "RPR": 1.3, "DDN": 1.0, "LDH": 1.4, "AGR": 1.2, "VNS": 1.2, "JOD": 1.0, "RJK": 1.1,
    "BRD": 1.4, "NSK": 1.2, "SLG": 0.8,
}

# How badly rain disrupts deliveries into a city: flood-prone metros, ghat
# roads and the north-east high; arid west low. 1.0 = typical.
CITY_SENSITIVITY = {
    "MUM": 1.35, "CHE": 1.3, "KOC": 1.3, "MLR": 1.4, "GOA": 1.25, "GUW": 1.35, "SLG": 1.35,
    "KOL": 1.25, "PAT": 1.2, "TVM": 1.2, "BLR": 1.15, "BBS": 1.15, "HYD": 1.1, "PUN": 1.1,
    "VSK": 1.1, "DDN": 1.2, "SRT": 1.15, "NSK": 1.05, "JOD": 0.7, "JAI": 0.8, "RJK": 0.85,
    "AMD": 0.9, "AGR": 0.9, "LDH": 0.9, "CHD": 0.95,
}

TIERS = ("express", "standard", "economy")
TIER_MIX = (0.20, 0.65, 0.15)
# promised = normal ETA x factor + hours: the slack a weather delay has to eat through.
TIER_PROMISE = {"express": (1.25, 0.0), "standard": (1.5, 6.0), "economy": (2.0, 18.0)}

CATEGORIES = (
    ("ELEC", "Electronics", 0.18, 4200),
    ("FASH", "Fashion", 0.24, 1100),
    ("GROC", "Groceries", 0.22, 650),
    ("HOME", "Home & Kitchen", 0.16, 1600),
    ("BEAU", "Beauty", 0.12, 700),
    ("BOOK", "Books & Toys", 0.08, 550),
)  # id, name, share of orders, average order value (INR)


@dataclass(frozen=True)
class Warehouse:
    id: str
    name: str
    city_id: str
    lat: float
    lon: float


@dataclass(frozen=True)
class DeliveryHub:
    id: str
    name: str
    city_id: str
    lat: float
    lon: float


@dataclass(frozen=True)
class Route:
    id: str
    code: str
    warehouse_id: str
    hub_id: str
    city_id: str
    role: str           # primary | secondary
    share: float        # of the city's orders
    distance_km: float
    sensitivity: float
    cities: tuple       # city ids along the way, origin first, destination last


class Network:
    def __init__(self, cities, warehouses, hubs, routes):
        self.cities = {c.id: c for c in cities}
        self.warehouses = {w.id: w for w in warehouses}
        self.hubs = {h.id: h for h in hubs}
        self.routes = {r.id: r for r in routes}
        self.route_by_code = {r.code: r for r in routes}
        self.routes_to = {}
        for r in routes:
            self.routes_to.setdefault(r.city_id, []).append(r)
        for rs in self.routes_to.values():
            rs.sort(key=lambda r: r.role != "primary")
        self.states = sorted({c.state for c in cities})


def _corridor_cities(a, b):
    """City ids within CORRIDOR_KM of the straight a->b line, in travel order."""
    length = geo.haversine_km(a.lat, a.lon, b.lat, b.lon)
    found = []
    for c in CITIES:
        if c.id in (a.id, b.id):
            continue
        da = geo.haversine_km(a.lat, a.lon, c.lat, c.lon)
        db = geo.haversine_km(b.lat, b.lon, c.lat, c.lon)
        # Distance to the segment via the triangle's height (fine at these scales).
        if da + db > length + 2 * CORRIDOR_KM or length == 0:
            continue
        s = (da + db + length) / 2
        height = 2 * max(s * (s - da) * (s - db) * (s - length), 0) ** 0.5 / length
        if height <= CORRIDOR_KM:
            found.append((da, c.id))
    return tuple(cid for _, cid in sorted(found))


def build_network(seed=11):
    rand = random.Random(seed)
    cities = {c.id: c for c in CITIES}
    parks = {h.city_id: h for h in HUBS}
    warehouses = [Warehouse(f"FC-{cid}", f"{parks[cid].name} FC", cid, parks[cid].lat, parks[cid].lon)
                  for cid in WAREHOUSE_CITIES]
    hubs = [DeliveryHub(f"HUB-{c.id}", f"{c.name} delivery hub", c.id,
                        *(geo.destination(c.lat, c.lon, rand.uniform(0, 360), rand.uniform(4, 9))))
            for c in CITIES]
    hubs = [DeliveryHub(h.id, h.name, h.city_id, round(h.lat, 4), round(h.lon, 4)) for h in hubs]
    hub_of = {h.city_id: h for h in hubs}

    routes = []
    for c in CITIES:
        nearest = sorted(warehouses, key=lambda w: geo.haversine_km(w.lat, w.lon, c.lat, c.lon))[:2]
        for role, share, w in (("primary", 0.75, nearest[0]), ("secondary", 0.25, nearest[1])):
            origin = cities[w.city_id]
            if w.city_id == c.id:
                distance, along = LOCAL_KM, (c.id,)
            else:
                distance = geo.haversine_km(w.lat, w.lon, c.lat, c.lon) * ROAD_FACTOR
                along = (origin.id, *_corridor_cities(origin, c), c.id)
            base = (CITY_SENSITIVITY.get(origin.id, 1.0) + 2 * CITY_SENSITIVITY.get(c.id, 1.0)) / 3
            sensitivity = min(1.4, max(0.7, base + rand.uniform(-0.05, 0.05)
                                       + (0.05 if distance > 600 else 0)))
            routes.append(Route(
                id=f"R-{w.city_id}-{c.id}", code=f"{w.city_id} → {c.id}", warehouse_id=w.id,
                hub_id=hub_of[c.id].id, city_id=c.id, role=role, share=share,
                distance_km=round(distance, 1), sensitivity=round(sensitivity, 2), cities=along,
            ))
    return Network(CITIES, warehouses, hubs, routes)


NETWORK = build_network()


def normal_eta_h(route):
    return WAREHOUSE_DWELL_H + route.distance_km / LINEHAUL_KMPH + HUB_DWELL_H + LAST_MILE_H


def promised_h(route, tier):
    factor, extra = TIER_PROMISE[tier]
    return normal_eta_h(route) * factor + extra


def products(seed=5, count=300):
    rand = random.Random(seed)
    out = []
    per = count // len(CATEGORIES)
    for cat_id, name, _share, aov in CATEGORIES:
        for i in range(per):
            out.append({"id": f"{cat_id}-{i + 1:03d}", "category_id": cat_id,
                        "name": f"{name} item {i + 1:03d}",
                        "price": round(aov * rand.lognormvariate(0, 0.45), -1),
                        "weight_kg": round(rand.uniform(0.2, 6.0 if cat_id in ("ELEC", "HOME") else 2.0), 2)})
    return out


def customers(seed=6, count=20000):
    rand = random.Random(seed)
    ids, weights = zip(*DEMAND.items())
    first = ("Aarav", "Diya", "Ishaan", "Kavya", "Rohan", "Meera", "Arjun", "Ananya", "Vikram", "Sneha",
             "Rahul", "Pooja", "Karthik", "Nisha", "Aditya", "Priya", "Sanjay", "Lakshmi", "Imran", "Fatima")
    last = ("Sharma", "Iyer", "Reddy", "Patel", "Das", "Nair", "Singh", "Gupta", "Rao", "Khan",
            "Menon", "Joshi", "Bose", "Kulkarni", "Pillai", "Verma", "Mehta", "Chatterjee")
    return [{"id": f"C{i + 1:06d}", "name": f"{rand.choice(first)} {rand.choice(last)}",
             "city_id": rand.choices(ids, weights)[0],
             "segment": rand.choices(("regular", "prime", "business"), (0.6, 0.32, 0.08))[0]}
            for i in range(count)]


def vehicles(seed=8):
    rand = random.Random(seed)
    out = []
    for w in NETWORK.warehouses.values():
        out += [{"id": f"TRK-{w.city_id}-{i + 1:02d}", "type": "truck", "base_id": w.id,
                 "capacity": rand.choice((300, 400, 600))} for i in range(14)]
    for h in NETWORK.hubs.values():
        n = max(4, round(DEMAND[h.city_id] * 3))
        out += [{"id": f"VAN-{h.city_id}-{i + 1:02d}", "type": rand.choice(("van", "van", "bike")),
                 "base_id": h.id, "capacity": 40} for i in range(n)]
    return out
