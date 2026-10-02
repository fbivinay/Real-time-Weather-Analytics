from datetime import datetime, timedelta, timezone

import pytest

from weatherops import geo, impact
from weatherops.network import NETWORK

IST = timezone(timedelta(hours=5, minutes=30))
AFTERNOON = datetime(2026, 10, 1, 14, 0, tzinfo=IST)
MODEL = impact.ImpactModel(NETWORK)


def zero_scores():
    return {s.id: 0 for s in NETWORK.stations.values()}, {h: 0 for h in NETWORK.hubs}


def lastmile(hub_id):
    return [r for r in NETWORK.routes.values() if r.kind == "lastmile" and r.hub_ids == (hub_id,)]


def test_calm_network_has_nothing_affected():
    stations, hubs = zero_scores()
    out = MODEL.evaluate(stations, hubs, AFTERNOON)
    k = out["kpis"]
    assert k["routes_affected"] == {"linehaul": 0, "lastmile": 0}
    assert k["deliveries_at_risk"] == 0
    assert k["hubs_affected"] == 0
    assert k["locations_high"] == 0
    assert k["deliveries_active"] > 0


def test_storm_over_a_hub_hits_its_last_mile_routes():
    stations, hubs = zero_scores()
    for s in NETWORK.sensors_by_hub["CHE"]:
        stations[s.id] = 80
    hubs["CHE"] = 80
    out = MODEL.evaluate(stations, hubs, AFTERNOON)
    routes = lastmile("CHE")
    for r in routes:
        assert out["routes"][r.id]["status"] == "critical"
        assert out["routes"][r.id]["region"] == "CHE"
    expected = sum(out["routes"][r.id]["active"] for r in routes)
    assert out["kpis"]["deliveries_at_risk"] >= expected > 0
    assert out["kpis"]["routes_affected"]["lastmile"] == len(routes)
    assert out["hubs"]["CHE"]["status"] == "critical"
    assert out["kpis"]["hubs_affected"] == 1


def test_city_reference_covers_routes_within_its_radius():
    stations, hubs = zero_scores()
    stations["REF-CHE"] = 60
    out = MODEL.evaluate(stations, hubs, AFTERNOON)
    che = NETWORK.cities["CHE"]
    near = [r for r in NETWORK.routes.values()
            if any(geo.haversine_km(che.lat, che.lon, *p) <= 40 for p in r.samples)]
    assert near
    for r in near:
        assert out["routes"][r.id]["status"] in {"high", "critical"}, r.id


def test_linehaul_region_follows_its_worst_point():
    stations, hubs = zero_scores()
    stations["REF-NLR"] = 85
    out = MODEL.evaluate(stations, hubs, AFTERNOON)
    nh16 = out["routes"]["LH-CHE-VJA"]
    assert nh16["status"] == "critical"
    assert nh16["region"] == "NLR"
    assert 0 < nh16["exposure"] < 1


def test_points_far_from_every_station_have_no_score():
    assert MODEL.point_score((15.0, 68.0), {"REF-MUM": 90}) is None


def test_untrusted_stations_are_simply_absent():
    stations, hubs = zero_scores()
    del stations["REF-CHE"]
    out = MODEL.evaluate(stations, hubs, AFTERNOON)
    assert out["routes"]["LM-CHE-1"]["status"] == "low"


def test_unknown_hub_score_is_unknown_not_low():
    stations, hubs = zero_scores()
    hubs["CHE"] = None
    assert MODEL.evaluate(stations, hubs, AFTERNOON)["hubs"]["CHE"]["status"] == "unknown"


def test_last_mile_is_quiet_at_night_and_linehaul_busy():
    lm = lastmile("BLR")[0]
    lh = NETWORK.routes["LH-BLR-CHE"]
    night = datetime(2026, 10, 1, 3, 0, tzinfo=IST)
    two_am = datetime(2026, 10, 1, 2, 0, tzinfo=IST)
    assert impact.active_deliveries(lm, night) * 5 < impact.active_deliveries(lm, AFTERNOON)
    assert impact.active_deliveries(lh, two_am) > impact.active_deliveries(lh, AFTERNOON)


def test_active_deliveries_are_deterministic():
    lm = lastmile("BLR")[0]
    assert impact.active_deliveries(lm, AFTERNOON) == impact.active_deliveries(lm, AFTERNOON)


def test_reroute_avoids_blocked_corridors():
    path, km = impact.reroute(NETWORK, {"LH-CHE-VJA"}, "CHE", "VJA")
    assert path[0] == "CHE" and path[-1] == "VJA"
    assert ("CHE", "VJA") not in list(zip(path, path[1:]))
    assert km > NETWORK.routes["LH-CHE-VJA"].length_km


def test_reroute_respects_a_detour_limit():
    direct = NETWORK.routes["LH-CHE-VJA"].length_km
    assert impact.reroute(NETWORK, {"LH-CHE-VJA"}, "CHE", "VJA", max_km=direct * 1.1) is None


def test_reroute_none_when_cut_off():
    blocked = {rid for _other, rid, _km in NETWORK.corridor_graph["GUW"]}
    assert impact.reroute(NETWORK, blocked, "GUW", "KOL") is None
