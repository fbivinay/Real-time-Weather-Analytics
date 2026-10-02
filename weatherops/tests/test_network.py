from collections import deque

from weatherops import geo
from weatherops.network import NETWORK, build_network, to_geojson


def test_counts_and_unique_ids():
    assert len(NETWORK.cities) == 40
    assert len(NETWORK.hubs) == 25
    station_ids = [s.id for s in NETWORK.references + NETWORK.sensors]
    assert len(station_ids) == len(set(station_ids)) == len(NETWORK.stations)
    route_ids = list(NETWORK.routes)
    assert len(route_ids) == len(set(route_ids))


def test_every_hub_belongs_to_a_known_city():
    for hub in NETWORK.hubs.values():
        assert hub.city_id in NETWORK.cities


def test_one_reference_station_per_city_at_the_city():
    assert len(NETWORK.references) == 40
    for ref in NETWORK.references:
        city = NETWORK.cities[ref.city_id]
        assert ref.id == f"REF-{city.id}"
        assert (ref.lat, ref.lon) == (city.lat, city.lon)
        assert ref.kind == "reference"
        assert ref.radius_km == 40


def test_sensors_sit_2_to_12_km_from_their_hub():
    for sensor in NETWORK.sensors:
        hub = NETWORK.hubs[sensor.hub_id]
        km = geo.haversine_km(hub.lat, hub.lon, sensor.lat, sensor.lon)
        assert 2 <= km <= 12.01, (sensor.id, km)
        assert sensor.city_id == hub.city_id
        assert sensor.radius_km == 10


def test_four_or_five_sensors_per_hub():
    counts = [len(NETWORK.sensors_by_hub[h]) for h in NETWORK.hubs]
    assert set(counts) <= {4, 5}
    assert 100 <= len(NETWORK.sensors) <= 125


def test_generation_is_deterministic():
    a, b = build_network(7), build_network(7)
    assert [(s.id, s.lat, s.lon) for s in a.sensors] == [(s.id, s.lat, s.lon) for s in b.sensors]
    assert [(r.id, r.capacity) for r in a.routes.values()] == [(r.id, r.capacity) for r in b.routes.values()]


def test_last_mile_routes_are_6_to_15_km_with_1_km_samples():
    lastmile = [r for r in NETWORK.routes.values() if r.kind == "lastmile"]
    assert len(lastmile) == 25 * 8
    for route in lastmile:
        assert 6 <= route.length_km <= 15.01
        gaps = [geo.haversine_km(*a, *b) for a, b in zip(route.samples, route.samples[1:])]
        assert max(gaps) <= 1.01
        assert 20 <= route.capacity <= 45


def test_linehaul_routes_sample_every_5_km():
    linehaul = [r for r in NETWORK.routes.values() if r.kind == "linehaul"]
    assert len(linehaul) >= 30
    for route in linehaul:
        gaps = [geo.haversine_km(*a, *b) for a, b in zip(route.samples, route.samples[1:])]
        assert max(gaps) <= 5.01
        assert 4 <= route.capacity <= 12
        assert len(route.hub_ids) == 2


def test_corridor_graph_connects_every_hub():
    start = next(iter(NETWORK.hubs))
    seen, queue = {start}, deque([start])
    while queue:
        for other, _route_id, _km in NETWORK.corridor_graph[queue.popleft()]:
            if other not in seen:
                seen.add(other)
                queue.append(other)
    assert seen == set(NETWORK.hubs)


def test_every_route_has_a_region():
    for route in NETWORK.routes.values():
        assert route.city_id in NETWORK.cities


def test_region_of_point_is_the_nearest_city():
    chennai = NETWORK.cities["CHE"]
    assert NETWORK.region_of_point(chennai.lat + 0.05, chennai.lon) == "CHE"


def test_geojson_export():
    fc = to_geojson(NETWORK)
    assert fc["type"] == "FeatureCollection"
    layers = {}
    for feature in fc["features"]:
        layers[feature["properties"]["layer"]] = layers.get(feature["properties"]["layer"], 0) + 1
        assert feature["geometry"]["type"] in {"Point", "LineString"}
    assert layers["hub"] == 25
    assert layers["station"] == len(NETWORK.stations)
    assert layers["route"] == len(NETWORK.routes)
