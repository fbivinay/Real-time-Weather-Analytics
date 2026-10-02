"""Weather -> delivery delay.

weather ETA = normal ETA x (1 + (m - 1) x route sensitivity), m by rain class;
weather-induced delay = weather ETA - normal ETA. A route's weather over a
trip is read hour by hour at the city the vehicle is passing (origin first,
destination last); the worst hour sets the trip's class.

Costs are simulated estimates in INR, not accounting figures.
"""
from datetime import timedelta

from weatherops import company as co
from weatherops.rainfall import CLASSES

MULTIPLIER = {"none": 1.0, "rain": 1.12, "heavy": 1.30, "extreme": 1.56}
SEVERITY_WEIGHT = {"none": 0.0, "rain": 0.35, "heavy": 0.7, "extreme": 1.0}
P_AFFECTED = {"none": 0.0, "rain": 0.45, "heavy": 0.8, "extreme": 0.95}   # share of exposed orders delayed
FAILED_RATE = {"none": 0.0, "rain": 0.002, "heavy": 0.008, "extreme": 0.03}  # failed first attempt
COST_PER_DELAY_HOUR = 38      # extra driver + vehicle time per order-hour of delay
COST_RESHIP = 220             # second attempt / reshipment
COST_SLA = {"express": 150, "standard": 60, "economy": 25}   # credit / compensation per breach
EXPOSED_P_RAIN = 0.5


def weather_eta_h(route, cls):
    return co.normal_eta_h(route) * (1 + (MULTIPLIER[cls] - 1) * route.sensitivity)


def weather_delay_h(route, cls):
    return weather_eta_h(route, cls) - co.normal_eta_h(route)


def expected_weight(probs):
    return sum(probs[c] * SEVERITY_WEIGHT[c] for c in CLASSES)


def expected_delay_h(route, probs):
    m = sum(probs[c] * MULTIPLIER[c] for c in CLASSES)
    return co.normal_eta_h(route) * (m - 1) * route.sensitivity


def city_at(route, fraction):
    cities = route.cities
    return cities[min(len(cities) - 1, round(fraction * (len(cities) - 1)))]


def trip_hours(route, dispatch):
    """(city, hour) pairs a trip passes through, one per started hour."""
    eta = co.normal_eta_h(route)
    n = max(1, int(eta + 0.999))
    return [(city_at(route, h / max(1, n - 1)), dispatch + timedelta(hours=h)) for h in range(n)]


def exposure(route, dispatch, now, forecast_fn):
    """Forecast weather of one trip. forecast_fn(city_id, when, lead_h) -> {probs, p_rain}."""
    worst, worst_w, exposed, last = None, -1.0, 0, None
    steps = trip_hours(route, dispatch)
    p_rain = 0.0
    for city_id, when in steps:
        f = forecast_fn(city_id, when, (when - now).total_seconds() / 3600)
        w = expected_weight(f["probs"])
        if w > worst_w:
            worst, worst_w = f["probs"], w
        exposed += f["p_rain"] >= EXPOSED_P_RAIN
        p_rain = max(p_rain, f["p_rain"])
        last = f
    return {
        "probs": worst,
        "weight": round(worst_w, 4),
        "expected_class": max(CLASSES, key=lambda c: worst[c]),
        "p_rain": round(p_rain, 4),
        "exposed_share": round(exposed / len(steps), 4),
        "delivery_p_rain": last["p_rain"],
        "hours": len(steps),
    }


def actual_class(route, dispatch, hourly):
    """Truth: worst observed class over the trip (simulator)."""
    from weatherops.rainfall import hourly_class
    classes = [hourly_class(hourly.mmph(city_id, when)) for city_id, when in trip_hours(route, dispatch)]
    return max(classes, key=CLASSES.index)
