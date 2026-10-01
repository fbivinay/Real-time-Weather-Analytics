from weatherops import actions
from weatherops.network import NETWORK


def assessment(category="high", hazard="rain", score=60, developing=False, **inputs):
    base = {"rain_mmph": 20.0, "rain_accum_mm": 30.0, "gust_kmph": 30.0, "temperature_c": 27.0,
            "humidity_pct": 90.0, "visibility_m": 4000.0}
    base.update(inputs)
    return {"score": score, "category": category, "hazard": hazard, "developing": developing,
            "forecast_category": "high" if developing else None, "inputs": base}


def lastmile_routes(n=3, active=40):
    return [{"id": f"LM-CHE-{i + 1}", "kind": "lastmile", "name": f"Sriperumbudur last-mile {i + 1}",
             "active": active, "status": "high"} for i in range(n)]


def impact(routes=(), hubs=(), reroutes=None, hub_status=None):
    routes = list(routes)
    return {"routes": routes, "hubs": list(hubs), "reroutes": reroutes or {},
            "deliveries_at_risk": sum(r["active"] for r in routes),
            "hub_status": hub_status or {h: "low" for h in NETWORK.hubs}}


def texts(acts):
    return [a["text"] for a in acts]


def test_heavy_rain_on_last_mile_pauses_two_wheelers_with_counts():
    acts = actions.recommend("CHE", "Chennai", assessment(), impact(lastmile_routes()), NETWORK)
    pause = [a for a in acts if "two-wheeler" in a["text"]]
    assert len(pause) == 1
    assert "3 routes" in pause[0]["text"] and "120 deliveries" in pause[0]["text"]
    assert pause[0]["priority"] == "P2" and pause[0]["owner"] == "last-mile ops"
    assert set(pause[0]["assets"]) == {"LM-CHE-1", "LM-CHE-2", "LM-CHE-3"}
    assert "20 mm/h" in pause[0]["because"]


def test_critical_rain_at_a_hub_triggers_waterlogging_sop_with_an_alternate():
    hubs = [{"id": "CHE", "name": "Sriperumbudur", "status": "critical"}]
    acts = actions.recommend("CHE", "Chennai", assessment("critical", score=88),
                             impact(lastmile_routes(), hubs), NETWORK)
    sop = [a for a in acts if "Waterlogging" in a["text"]][0]
    assert sop["priority"] == "P1" and sop["owner"] == "warehouse"
    assert "Sriperumbudur" in sop["text"]
    alt = [h for h in NETWORK.hubs.values() if h.name in sop["text"] and h.id != "CHE"]
    assert alt, sop["text"]


def test_linehaul_hold_includes_a_reroute_when_one_exists():
    route = {"id": "LH-CHE-VJA", "kind": "linehaul", "name": "NH16 Chennai–Vijayawada", "active": 6,
             "status": "high"}
    acts = actions.recommend("NLR", "Nellore", assessment(), impact([route],
                             reroutes={"LH-CHE-VJA": (["CHE", "BLR", "HYD", "VJA"], 1020.0)}), NETWORK)
    hold = [a for a in acts if "Hold departures" in a["text"]][0]
    assert "NH16 Chennai–Vijayawada" in hold["text"]
    assert "Hoskote" in hold["text"] and "1020 km" in hold["text"]
    assert hold["owner"] == "linehaul"


def test_fog_delays_linehaul_departures():
    route = {"id": "LH-DEL-JAI", "kind": "linehaul", "name": "NH48 Delhi–Jaipur", "active": 8, "status": "critical"}
    acts = actions.recommend("DEL", "Delhi", assessment("critical", "fog", 85, visibility_m=150),
                             impact([route]), NETWORK)
    assert any("visibility > 500 m" in t for t in texts(acts))
    assert not any("Hold departures" in t for t in texts(acts))


def test_wind_notifies_drivers_with_the_gust_value():
    acts = actions.recommend("CHE", "Chennai", assessment("high", "wind", 62, gust_kmph=74.6),
                             impact(lastmile_routes()), NETWORK)
    notify = [a for a in acts if "Notify drivers" in a["text"]][0]
    assert "75 km/h" in notify["text"] and notify["owner"] == "fleet"


def test_heat_shifts_delivery_slots():
    acts = actions.recommend("DEL", "Delhi", assessment("high", "heat", 63, temperature_c=46, humidity_pct=15),
                             impact(lastmile_routes()), NETWORK)
    assert any("before 11:00" in t for t in texts(acts))


def test_developing_risk_is_a_p3_pre_alert():
    acts = actions.recommend("CHE", "Chennai", assessment("low", "rain", 10, developing=True), impact(), NETWORK)
    assert [a["priority"] for a in acts] == ["P3"]
    assert "within 60 min" in acts[0]["text"]


def test_low_risk_recommends_nothing():
    assert actions.recommend("CHE", "Chennai", assessment("low", None, 5), impact(), NETWORK) == []


def test_actions_are_sorted_by_priority():
    hubs = [{"id": "CHE", "name": "Sriperumbudur", "status": "critical"}]
    acts = actions.recommend("CHE", "Chennai", assessment("critical", score=88),
                             impact(lastmile_routes(), hubs), NETWORK)
    prios = [a["priority"] for a in acts]
    assert prios == sorted(prios)
    assert len({a["id"] for a in acts}) == len(acts)
