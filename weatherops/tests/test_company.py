import pytest

from weatherops import company as co


def test_every_city_has_a_hub_and_two_warehouse_routes():
    net = co.NETWORK
    assert len(net.hubs) == 40 and len(net.warehouses) == 10
    for city_id in net.cities:
        routes = net.routes_to[city_id]
        assert [r.role for r in routes] == ["primary", "secondary"]
        assert routes[0].warehouse_id != routes[1].warehouse_id
        assert sum(r.share for r in routes) == pytest.approx(1)


def test_relations_are_consistent():
    net = co.NETWORK
    for r in net.routes.values():
        assert r.warehouse_id in net.warehouses
        assert net.hubs[r.hub_id].city_id == r.city_id
        assert 0.7 <= r.sensitivity <= 1.4
        assert r.cities[0] == net.warehouses[r.warehouse_id].city_id and r.cities[-1] == r.city_id


def test_blr_mys_normal_eta_matches_the_brief():
    r = co.NETWORK.route_by_code["BLR → MYS"]
    assert r.distance_km == pytest.approx(190, abs=15)
    assert co.normal_eta_h(r) == pytest.approx(5.8, abs=0.4)


def test_sla_promise_slack_orders_by_tier():
    r = co.NETWORK.route_by_code["BLR → MYS"]
    slacks = [co.promised_h(r, tier) - co.normal_eta_h(r) for tier in co.TIERS]
    assert slacks == sorted(slacks) and slacks[0] > 0


def test_catalogue_and_fleet_are_deterministic():
    assert co.products() == co.products()
    assert len(co.products()) == 300
    assert {p["category_id"] for p in co.products()} == {c[0] for c in co.CATEGORIES}
    fleet = co.vehicles()
    assert all(v["base_id"] in co.NETWORK.warehouses or v["base_id"] in co.NETWORK.hubs for v in fleet)
