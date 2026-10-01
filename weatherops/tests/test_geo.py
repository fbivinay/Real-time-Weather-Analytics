from weatherops import geo


def test_haversine_delhi_to_mumbai_is_about_1150_km():
    km = geo.haversine_km(28.6139, 77.2090, 19.0760, 72.8777)
    assert abs(km - 1148) < 10


def test_haversine_same_point_is_zero():
    assert geo.haversine_km(12.97, 77.59, 12.97, 77.59) == 0


def test_destination_lands_at_the_requested_distance_and_bearing():
    lat, lon = geo.destination(12.97, 77.59, 90, 10)
    assert abs(geo.haversine_km(12.97, 77.59, lat, lon) - 10) < 0.01
    assert lon > 77.59
    assert abs(lat - 12.97) < 0.01


def test_destination_north_increases_latitude():
    lat, lon = geo.destination(12.97, 77.59, 0, 5)
    assert lat > 12.97
    assert abs(lon - 77.59) < 1e-9


def test_sample_polyline_spacing_and_endpoints():
    start = (20.0, 78.0)
    end = geo.destination(*start, 90, 100)
    points = geo.sample_polyline([start, end], 5)
    assert len(points) == 21
    assert points[0] == start
    assert points[-1] == end
    gaps = [geo.haversine_km(*a, *b) for a, b in zip(points, points[1:])]
    assert max(gaps) <= 5.01


def test_sample_polyline_follows_every_segment():
    a = (20.0, 78.0)
    b = geo.destination(*a, 90, 12)
    c = geo.destination(*b, 0, 7)
    points = geo.sample_polyline([a, b, c], 5)
    assert b in points
    assert points[-1] == c
    gaps = [geo.haversine_km(*p, *q) for p, q in zip(points, points[1:])]
    assert max(gaps) <= 5.01


def test_sample_polyline_single_point():
    assert geo.sample_polyline([(10.0, 70.0)], 5) == [(10.0, 70.0)]


def test_polyline_length_sums_segments():
    a = (20.0, 78.0)
    b = geo.destination(*a, 90, 12)
    c = geo.destination(*b, 0, 7)
    assert abs(geo.polyline_length_km([a, b, c]) - 19) < 0.01
