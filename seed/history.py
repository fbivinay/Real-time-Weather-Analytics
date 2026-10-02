"""Synthetic ShopFlow delivery history, aggregated per day x route x category,
driven by the real daily rainfall of the cities each route passes.

Volume grows ~25 % a year (9k orders/day in Jan 2021 -> ~25k by mid 2025)
with weekly and festive-season patterns. Weather enters only through the
delay model shared with the live system (weatherops.delay), so history,
live operations and predictions tell one consistent story.
"""
import math

import numpy as np
import pandas as pd

from weatherops import company as co
from weatherops import delay, risk
from weatherops.rainfall import CLASSES, daily_class, forecast_probs

BASE_SLA_RATE = 0.022        # promise misses unrelated to weather
BASE_DELAYED_RATE = 0.055    # > 30 min late unrelated to weather
DELAY_SIGMA = 0.3            # spread of weather delay around its expected value
SLA_COST_AVG = sum(m * delay.COST_SLA[t] for t, m in zip(co.TIERS, co.TIER_MIX))


def _p_exposed(cls, mm):
    if cls == "none":
        return 0.0
    if cls == "rain":
        return min(0.9, 0.25 + mm / 80)
    return 0.9 if cls == "heavy" else 0.97


def _p_over(threshold, mean):
    """P(lognormal delay with this mean > threshold)."""
    if mean <= 0:
        return 0.0
    mu = math.log(mean) - DELAY_SIGMA ** 2 / 2
    z = (math.log(threshold) - mu) / DELAY_SIGMA
    return 0.5 * math.erfc(z / math.sqrt(2))


def build(days, rain, seed=0):
    """days: list of dates; rain: {(city_id, 'YYYY-MM-DD'): mm}. Returns a DataFrame."""
    rng = np.random.default_rng(seed)
    routes = list(co.NETWORK.routes.values())
    total_demand = sum(co.DEMAND.values())
    cats = co.CATEGORIES
    cat_share = np.array([c[2] for c in cats])

    # Per route constants.
    route_weight = np.array([co.DEMAND[r.city_id] / total_demand * r.share for r in routes])
    delay_mean = {c: np.array([delay.weather_delay_h(r, c) * 60 for r in routes]) for c in CLASSES}
    p_wb = {c: np.array([sum(m * _p_over(max(1e-6, (co.promised_h(r, t) - co.normal_eta_h(r)) * 60), dm)
                             for t, m in zip(co.TIERS, co.TIER_MIX))
                         for r, dm in zip(routes, delay_mean[c])]) for c in CLASSES}
    p_late = {c: np.array([_p_over(30, dm) for dm in delay_mean[c]]) for c in CLASSES}

    frames = []
    for day in days:
        key = day.isoformat()
        mm = np.array([max((rain.get((cid, key)) or 0.0) for cid in r.cities) for r in routes])
        cls = [daily_class(v) for v in mm]
        lam = co.national_orders(day) * route_weight * rng.lognormal(0, 0.12, len(routes))
        orders = rng.poisson(np.outer(lam, cat_share))                               # routes x cats
        p_exp = np.array([_p_exposed(c, v) for c, v in zip(cls, mm)])[:, None]
        exposed = rng.binomial(orders, p_exp)
        p_aff = np.array([delay.P_AFFECTED[c] for c in cls])[:, None]
        affected = rng.binomial(exposed, p_aff)
        mean = np.array([delay_mean[c][i] for i, c in enumerate(cls)])[:, None]
        delay_sum = affected * mean * rng.lognormal(0, 0.15, affected.shape)
        wb = rng.binomial(affected, np.array([p_wb[c][i] for i, c in enumerate(cls)])[:, None])
        late_w = rng.binomial(affected, np.array([p_late[c][i] for i, c in enumerate(cls)])[:, None])
        breaches = np.minimum(orders, rng.binomial(orders, BASE_SLA_RATE) + wb)
        delayed = np.minimum(orders, rng.binomial(orders, BASE_DELAYED_RATE) + late_w)
        fail = np.array([delay.FAILED_RATE[c] for c in cls])[:, None]
        frames.append(pd.DataFrame({
            "date": day,
            "route_id": np.repeat([r.id for r in routes], len(cats)),
            "category_id": np.tile([c[0] for c in cats], len(routes)),
            "severity": np.repeat(cls, len(cats)),
            "rain_mm": np.repeat(mm.round(1), len(cats)),
            "orders": orders.ravel(),
            "exposed": exposed.ravel(),
            "affected": affected.ravel(),
            "delayed": delayed.ravel(),
            "sla_breaches": breaches.ravel(),
            "delay_min_sum": delay_sum.ravel().round(1),
            "cost_transport": (delay_sum / 60 * delay.COST_PER_DELAY_HOUR).ravel().round(0),
            "cost_reship": (exposed * fail * delay.COST_RESHIP).ravel().round(0),
            "cost_sla": (wb * SLA_COST_AVG).ravel().round(0),
        }))
    f = pd.concat(frames, ignore_index=True)
    return f[f["orders"] > 0].reset_index(drop=True)


def route_impact(frame):
    """Per route x severity: weather delay as a share of normal ETA (among affected
    orders) and the share of orders delivered within their promise."""
    g = frame.groupby(["route_id", "severity"]).agg(
        days=("date", "nunique"), orders=("orders", "sum"), affected=("affected", "sum"),
        delay=("delay_min_sum", "sum"), breaches=("sla_breaches", "sum")).reset_index()
    normal = g["route_id"].map({r.id: co.normal_eta_h(r) * 60 for r in co.NETWORK.routes.values()})
    g["delay_ratio"] = np.where(g["affected"] > 0, g["delay"] / g["affected"].clip(lower=1) / normal, 0.0).round(4)
    g["on_time"] = (1 - g["breaches"] / g["orders"].clip(lower=1)).round(4)
    return g[["route_id", "severity", "days", "delay_ratio", "on_time"]]


def _auc(scores, labels):
    order = np.argsort(scores)
    ranks = np.empty(len(scores))
    ranks[order] = np.arange(1, len(scores) + 1)
    pos = labels.sum()
    neg = len(labels) - pos
    return float((ranks[labels].sum() - pos * (pos + 1) / 2) / (pos * neg))


def fit_breach_model(seed=0, n=200_000):
    """Fit P(SLA breach) on simulated orders: true rain class, a forecast made
    1-48 h ahead (the model only sees the forecast), lognormal weather delay
    plus non-weather delay. Returns (coefficients, metrics)."""
    rng = np.random.default_rng(seed)
    routes = list(co.NETWORK.routes.values())
    w = np.array([co.DEMAND[r.city_id] * r.share for r in routes])
    picks = rng.choice(len(routes), n, p=w / w.sum())
    tiers = rng.choice(len(co.TIERS), n, p=co.TIER_MIX)
    truth = rng.choice(len(CLASSES), n, p=[0.55, 0.30, 0.12, 0.03])
    leads = rng.uniform(1, 48, n)
    X = np.empty((n, 4))
    y = np.empty(n, bool)
    noise = rng.lognormal(-DELAY_SIGMA ** 2 / 2, DELAY_SIGMA, n)
    other = rng.exponential(0.12, n)
    for i in range(n):
        route, tier, cls = routes[picks[i]], co.TIERS[tiers[i]], CLASSES[truth[i]]
        probs = forecast_probs(cls, leads[i])
        X[i] = risk.features(route, tier, {"weight": delay.expected_weight(probs)})
        actual = delay.weather_delay_h(route, cls) * noise[i] + other[i]
        y[i] = actual > co.promised_h(route, tier) - co.normal_eta_h(route)
    coefs = risk.fit_logistic(X, y)
    p = 1 / (1 + np.exp(-(X @ coefs)))
    return [round(float(c), 4) for c in coefs], {
        "n": n, "base_rate": round(float(y.mean()), 4), "auc": round(_auc(p, y), 4),
        "features": ["intercept", "slack_ratio", "sensitivity_x_expected_severity", "distance_1000km"],
    }
