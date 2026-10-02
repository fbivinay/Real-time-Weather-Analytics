"""Future-order delivery risk: an additive, explainable 0-100 score.

Every point is attributed to one named contributor, each with a fixed cap, so
an analyst can read why an order is risky ("rainfall severity +24, route
history +15 ..."). SLA breach probability comes from a logistic regression
fitted on the synthetic delivery history (see seed job).
"""
import math

import numpy as np

from weatherops import company as co
from weatherops import delay

CAPS = {"rainfall": 30, "route_history": 20, "exposure": 15, "load": 10,
        "timing": 8, "sla": 12, "performance": 5}
LABELS = {"rainfall": "Rainfall severity", "route_history": "Historical route impact",
          "exposure": "Route exposure", "load": "Warehouse load", "timing": "Delivery timing",
          "sla": "SLA tightness", "performance": "Route on-time history"}
EDGES = ((75, "Critical"), (50, "High"), (25, "Medium"))
IST_HOURS = 5.5


def category(score):
    return next((name for edge, name in EDGES if score >= edge), "Low")


def features(route, tier, wx):
    """[1, slack ratio, sensitivity x expected severity, distance / 1000 km]."""
    normal = co.normal_eta_h(route)
    slack = co.promised_h(route, tier) - normal
    return [1.0, slack / normal, route.sensitivity * wx["weight"], route.distance_km / 1000]


def breach_probability(coefs, x):
    z = sum(c * v for c, v in zip(coefs, x))
    return 1 / (1 + math.exp(-max(-30, min(30, z))))


def fit_logistic(X, y, iters=25):
    """Newton / IRLS; returns the coefficient vector."""
    X = np.asarray(X, float)
    y = np.asarray(y, float)
    w = np.zeros(X.shape[1])
    for _ in range(iters):
        p = 1 / (1 + np.exp(-np.clip(X @ w, -30, 30)))
        grad = X.T @ (y - p)
        hess = X.T @ (X * (p * (1 - p))[:, None]) + 1e-6 * np.eye(X.shape[1])
        step = np.linalg.solve(hess, grad)
        w += step
        if np.abs(step).max() < 1e-8:
            break
    return w


def assess(route, tier, dispatch, wx, hist, wh_exposed_share, coefs):
    """wx: delay.exposure(...) of this trip; hist: {class: {delay_ratio, on_time}} for the route."""
    probs = wx["probs"]
    normal = co.normal_eta_h(route)
    slack_h = co.promised_h(route, tier) - normal
    exp_delay_h = delay.expected_delay_h(route, probs)
    wet = max(("rain", "heavy", "extreme"), key=lambda c: probs[c])
    arrival_ist = (dispatch.hour + IST_HOURS + normal) % 24
    evening = arrival_ist >= 17 or arrival_ist < 6

    raw = {
        "rainfall": CAPS["rainfall"] * wx["weight"],
        "route_history": CAPS["route_history"] * wx["p_rain"] * min(1.0, hist[wet]["delay_ratio"] / 0.5),
        "exposure": CAPS["exposure"] * wx["exposed_share"] * (0.5 + 0.5 * min(1.0, normal / 10)),
        "load": CAPS["load"] * min(1.0, wh_exposed_share / 0.6),
        "timing": CAPS["timing"] * wx["delivery_p_rain"] * (1.0 if evening else 0.6),
        "sla": CAPS["sla"] * min(1.0, exp_delay_h / slack_h) if slack_h > 0 else CAPS["sla"],
        "performance": CAPS["performance"] * min(1.0, max(0.0, 1 - hist["none"]["on_time"]) / 0.25),
    }
    details = {
        "rainfall": f"{wx['expected_class']} rain expected on the trip ({round(wx['p_rain'] * 100)}% chance of rain)",
        "route_history": f"{route.code} historically runs {round(hist[wet]['delay_ratio'] * 100)}% slower in {wet} rain",
        "exposure": f"{round(wx['exposed_share'] * 100)}% of the {wx['hours']} h trip under likely rain",
        "load": f"{round(wh_exposed_share * 100)}% of this warehouse's upcoming orders are weather-exposed",
        "timing": f"arrives around {int(arrival_ist):02d}:00 IST" + (" (evening last mile)" if evening else ""),
        "sla": f"expected delay {round(exp_delay_h * 60)} min against {round(slack_h * 60)} min of SLA slack",
        "performance": f"{round(hist['none']['on_time'] * 100)}% on time in dry weather",
    }
    contributions = [{"key": k, "label": LABELS[k], "points": round(v, 1), "cap": CAPS[k], "detail": details[k]}
                     for k, v in raw.items()]
    contributions.sort(key=lambda c: -c["points"])
    score = round(min(100.0, sum(raw.values())), 1)
    return {
        "score": score,
        "category": category(score),
        "expected_class": wx["expected_class"],
        "p_rain": wx["p_rain"],
        "expected_delay_min": round(exp_delay_h * 60),
        "p_breach": round(breach_probability(coefs, features(route, tier, wx)), 4),
        "contributions": contributions,
    }
