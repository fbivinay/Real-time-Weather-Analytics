"""Recommended operational actions from a fixed rule table - what a
dispatcher should consider, with the numbers that justify it. Nothing here
is sent to drivers or customers; it is decision support.

Priority follows the region's category: P1 Critical, P2 High, P3 a
pre-alert for risk the forecast expects within the hour.
"""
from weatherops.risk import RANK

RULES = (
    # key, hazards, minimum category, asset kind, owner, template
    ("pause-2w", ("rain",), "high", "lastmile", "last-mile ops",
     "Pause two-wheeler dispatch in {region} ({n_routes} routes, {deliveries} deliveries); "
     "notify customers of delay"),
    ("waterlogging", ("rain",), "critical", "hub", "warehouse",
     "Waterlogging SOP at {hub}: raise ground-level stock, divert inbound to {alt_hub}"),
    ("hold-linehaul", ("rain", "wind"), "high", "linehaul", "linehaul",
     "Hold departures on {route} for 60 min{reroute}"),
    ("notify-wind", ("wind",), "high", "any", "fleet",
     "Notify drivers on {n_routes} routes in {region}: gusts {gust} km/h, reduce speed, "
     "no high-sided loads"),
    ("fog-convoy", ("fog",), "high", "linehaul", "linehaul",
     "Delay departures until visibility > 500 m; convoy on {routes}"),
    ("heat-slots", ("heat",), "high", "lastmile", "last-mile ops",
     "Shift delivery slots to before 11:00 / after 16:00 in {region}; check cold-chain loads"),
)
PRE_ALERT = ("Pre-alert: {hazard} expected in {region} within 60 min; stage drivers, hold new dispatch")


def _because(a):
    i = a.get("inputs") or {}
    parts = []
    if i.get("rain_mmph"):
        parts.append(f"rain {i['rain_mmph']:.0f} mm/h")
    if i.get("gust_kmph"):
        parts.append(f"gust {i['gust_kmph']:.0f} km/h")
    if i.get("visibility_m") is not None and i["visibility_m"] < 2000:
        parts.append(f"visibility {i['visibility_m']:.0f} m")
    if i.get("temperature_c") is not None and a.get("hazard") == "heat":
        parts.append(f"{i['temperature_c']:.0f} °C")
    parts.append(f"risk {a['score']}")
    return ", ".join(parts)


def _alt_hub(network, hub_id, hub_status):
    for other, _route, _km in sorted(network.corridor_graph[hub_id], key=lambda x: x[2]):
        if RANK.get(hub_status.get(other, "low"), 0) < RANK["high"]:
            return network.hubs[other].name
    return "the nearest unaffected hub"


def recommend(region_id, region_name, assessment, impact_slice, network):
    hazard, cat = assessment.get("hazard"), assessment["category"]
    out = []
    if hazard and RANK[cat] >= RANK["high"]:
        priority = "P1" if cat == "critical" else "P2"
        because = _because(assessment)
        routes = impact_slice.get("routes", [])
        by_kind = {"lastmile": [r for r in routes if r["kind"] == "lastmile"],
                   "linehaul": [r for r in routes if r["kind"] == "linehaul"]}
        for key, hazards, minimum, kind, owner, template in RULES:
            if hazard not in hazards or RANK[cat] < RANK[minimum]:
                continue

            def action(suffix, text, assets):
                out.append({"id": f"{region_id}:{key}:{suffix}", "priority": priority, "owner": owner,
                            "text": text, "assets": assets, "because": because})

            if kind == "hub":
                for hub in impact_slice.get("hubs", []):
                    if RANK.get(hub["status"], 0) >= RANK[minimum]:
                        action(hub["id"], template.format(
                            hub=hub["name"],
                            alt_hub=_alt_hub(network, hub["id"], impact_slice.get("hub_status", {}))),
                            [hub["id"]])
            elif key == "hold-linehaul":
                for r in by_kind["linehaul"]:
                    reroute = ""
                    found = impact_slice.get("reroutes", {}).get(r["id"])
                    if found:
                        path, km = found
                        via = ", ".join(network.hubs[h].name for h in path[1:-1])
                        reroute = f"; reroute {r['active']} trucks via {via} ({km:.0f} km)"
                    action(r["id"], template.format(route=r["name"], reroute=reroute), [r["id"]])
            else:
                assets = routes if kind == "any" else by_kind[kind]
                if not assets:
                    continue
                gust = (assessment.get("inputs") or {}).get("gust_kmph") or 0
                action("all", template.format(
                    region=region_name, n_routes=len(assets),
                    deliveries=sum(r["active"] for r in assets), gust=f"{gust:.0f}",
                    routes=", ".join(r["name"] for r in assets)), [r["id"] for r in assets])

    elif assessment.get("developing") and hazard:
        out.append({"id": f"{region_id}:pre-alert:{hazard}", "priority": "P3", "owner": "dispatch",
                    "text": PRE_ALERT.format(hazard=hazard, region=region_name), "assets": [],
                    "because": f"forecast {assessment.get('forecast_category')} within 60 min"})
    return sorted(out, key=lambda a: a["priority"])
