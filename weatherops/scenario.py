"""What-if analysis over upcoming orders. Simulated impact / estimated outcome.

Levers (any combination):
  dispatch_shift_h  move planned dispatch by -4..+8 h (never before now)
  reroute           send weather-exposed orders from the other of the city's
                    two warehouses when that trip is drier
  add_hub           a temporary micro-fulfilment site in one city: its orders
                    ship locally instead of over the linehaul
  rain_scale        0.5-2x rainfall: shifts forecast probability one class
                    down or up by (scale - 1)

Inputs are order cohorts {route_id, city_id, tier, dispatch, count}; the same
delay and breach models as the live risk engine are applied to each.
"""
from dataclasses import replace
from datetime import timedelta

from weatherops import company as co
from weatherops import delay, risk
from weatherops.rainfall import CLASSES

LATE_MIN = 45
COST_PER_ORDER_KM = 0.15          # linehaul truck cost shared by ~300 orders
HUB_COST_PER_DAY = 150_000        # temporary micro-fulfilment site


def scaled(forecast_fn, scale):
    if scale == 1:
        return forecast_fn

    def fn(city_id, when, lead_h):
        f = forecast_fn(city_id, when, lead_h)
        p = [f["probs"][c] for c in CLASSES]
        out = p[:]
        k = min(1.0, abs(scale - 1))
        if scale > 1:      # move mass up a class
            for i in range(len(p) - 2, -1, -1):
                out[i + 1] += p[i] * k
                out[i] -= p[i] * k
        else:              # move mass down a class
            for i in range(1, len(p)):
                out[i - 1] += p[i] * k
                out[i] -= p[i] * k
        probs = dict(zip(CLASSES, out))
        return {**f, "probs": probs, "p_rain": 1 - probs["none"]}
    return fn


def local_route(city_id, network):
    base = network.routes_to[city_id][0]
    return replace(base, id=f"LOCAL-{city_id}", code=f"{city_id} local hub", role="local",
                   warehouse_id=f"MFC-{city_id}", distance_km=co.LOCAL_KM, cities=(city_id,),
                   sensitivity=round(max(0.7, base.sensitivity - 0.1), 2))


def evaluate(cohorts, now, forecast_fn, coefs, levers=None, network=co.NETWORK, window_h=48):
    levers = levers or {}
    fc = scaled(forecast_fn, levers.get("rain_scale", 1.0))
    shift = timedelta(hours=levers.get("dispatch_shift_h", 0))
    hub_city = levers.get("add_hub")
    totals = {"orders": 0, "exposed": 0, "late": 0, "sla_breaches": 0.0, "delay_min_sum": 0.0,
              "cost_delay": 0.0, "cost_sla": 0.0, "cost_reship": 0.0, "cost_ops": 0.0,
              "rerouted": 0, "local": 0}
    for c in cohorts:
        route = network.routes[c["route_id"]]
        dispatch = max(now, c["dispatch"] + shift)
        n = c["count"]
        if hub_city and c["city_id"] == hub_city:
            route = local_route(hub_city, network)
            totals["local"] += n
        wx = delay.exposure(route, dispatch, now, fc)
        if levers.get("reroute") and route.role != "local" and wx["exposed_share"] > 0:
            other = next(r for r in network.routes_to[c["city_id"]] if r.id != route.id)
            wx_other = delay.exposure(other, dispatch, now, fc)
            if wx_other["weight"] < wx["weight"]:
                totals["cost_ops"] += n * max(0.0, other.distance_km - route.distance_km) * COST_PER_ORDER_KM
                route, wx = other, wx_other
                totals["rerouted"] += n
        delay_h = delay.expected_delay_h(route, wx["probs"])
        p_breach = risk.breach_probability(coefs, risk.features(route, c["tier"], wx))
        totals["orders"] += n
        totals["exposed"] += n if wx["exposed_share"] > 0 else 0
        totals["late"] += n if delay_h * 60 >= LATE_MIN else 0
        totals["sla_breaches"] += n * p_breach
        totals["delay_min_sum"] += n * delay_h * 60
        totals["cost_delay"] += n * delay_h * delay.COST_PER_DELAY_HOUR
        totals["cost_sla"] += n * p_breach * delay.COST_SLA[c["tier"]]
        totals["cost_reship"] += n * sum(wx["probs"][k] * delay.FAILED_RATE[k] for k in CLASSES) * delay.COST_RESHIP
    if hub_city:
        totals["cost_ops"] += HUB_COST_PER_DAY * window_h / 24
    orders = max(1, totals["orders"])
    cost = totals["cost_delay"] + totals["cost_sla"] + totals["cost_reship"] + totals["cost_ops"]
    return {
        "orders": totals["orders"],
        "exposed": totals["exposed"],
        "late": totals["late"],
        "sla_breaches": round(totals["sla_breaches"]),
        "avg_delay_min": round(totals["delay_min_sum"] / orders, 1),
        "cost_inr": round(cost),
        "cost_breakdown": {k: round(totals[k]) for k in ("cost_delay", "cost_sla", "cost_reship", "cost_ops")},
        "rerouted": totals["rerouted"],
        "local": totals["local"],
    }
