"""SQL against a real, seeded and running WeatherOps database.

    TEST_PG_DSN="host=localhost port=15432 user=weatherops password=... dbname=weatherops" pytest serving/tests/test_queries_pg.py

(e.g. through `ssh -L 15432:<postgres-pod-ip>:5432`). Skipped without TEST_PG_DSN.
"""
import os

import psycopg
import pytest

from serving import queries as q

DSN = os.environ.get("TEST_PG_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_PG_DSN not set")


@pytest.fixture(scope="module")
def conn():
    with psycopg.connect(DSN, autocommit=True) as c:
        yield c


@pytest.fixture(scope="module")
def sim_now(conn):
    return q.one(conn, "SELECT max(sim_time) AS t FROM weather_risk")["t"]


def test_history_totals_and_filters_narrow(conn):
    everything = q.history(conn, {})
    assert everything["totals"]["orders"] > 10_000_000
    assert {s["season"] for s in everything["seasons"]} == {"pre-monsoon", "monsoon", "post-monsoon", "winter"}
    kerala = q.history(conn, {"state": "Kerala", "year": 2024})
    assert 0 < kerala["totals"]["orders"] < everything["totals"]["orders"]
    assert kerala["geography"]["level"] == "city"
    assert sum(m["orders"] for m in kerala["monthly"]) == kerala["totals"]["orders"]
    heavy = {s["severity"]: s for s in everything["severity"]}
    assert heavy["heavy"]["delay_per_exposed_min"] > heavy["rain"]["delay_per_exposed_min"]
    assert heavy["heavy"]["breach_rate"] > heavy["none"]["breach_rate"]


def test_options_and_rainfall_map(conn):
    opts = q.history_options(conn)
    assert 2021 in opts["years"] and len(opts["cities"]) == 40 and len(opts["routes"]) == 80
    july = q.rainfall_map(conn, 7)
    assert len(july["cities"]) == 40
    mum = next(c for c in july["cities"] if c["id"] == "MUM")
    jod = next(c for c in july["cities"] if c["id"] == "JOD")
    assert mum["monthly_total_mm"] > jod["monthly_total_mm"]
    assert len(q.structural_impact(conn)) == 40


def test_future_orders_are_ranked_and_explained(conn, sim_now):
    page = q.future_orders(conn, sim_now, 48, {}, "score", 1, 25)
    assert page["total"] > 1000 and len(page["rows"]) == 25
    scores = [r["score"] for r in page["rows"]]
    assert scores == sorted(scores, reverse=True)
    detail = q.order_detail(conn, page["rows"][0]["id"])
    assert detail["prediction"]["contributions"] and detail["items"]
    assert len(detail["prediction"]["contributions"]) == 7


def test_location_history_and_search(conn):
    h = q.location_history(conn, "city", "MUM")
    assert len(h["last_12_months"]) >= 12 and h["monsoon"]["orders"] > 0
    found = q.search(conn, "BLR")
    assert any(r["code"] == "BLR → MYS" for r in q.search(conn, "BLR → MYS")["routes"])
    assert found["cities"][0]["id"] == "BLR"
    assert q.search(conn, "Bhiwandi")["warehouses"]


def test_scenario_cohorts(conn, sim_now):
    cohorts = q.scenario_cohorts(conn, sim_now, 24)
    assert cohorts and all(c["count"] > 0 for c in cohorts)
