"""Spherical-earth helpers. Accurate to well under 0.5% at Indian latitudes,
which is far finer than anything downstream (40 km station radii) needs."""
import math

EARTH_RADIUS_KM = 6371.0088


def haversine_km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def destination(lat, lon, bearing_deg, distance_km):
    d = distance_km / EARTH_RADIUS_KM
    b = math.radians(bearing_deg)
    p1, l1 = math.radians(lat), math.radians(lon)
    p2 = math.asin(math.sin(p1) * math.cos(d) + math.cos(p1) * math.sin(d) * math.cos(b))
    l2 = l1 + math.atan2(
        math.sin(b) * math.sin(d) * math.cos(p1),
        math.cos(d) - math.sin(p1) * math.sin(p2),
    )
    return (math.degrees(p2), math.degrees(l2))


def _intermediate(a, b, f):
    """Point a fraction f of the way along the great circle from a to b."""
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    d = haversine_km(*a, *b) / EARTH_RADIUS_KM
    if d == 0:
        return a
    wa = math.sin((1 - f) * d) / math.sin(d)
    wb = math.sin(f * d) / math.sin(d)
    x = wa * math.cos(lat1) * math.cos(lon1) + wb * math.cos(lat2) * math.cos(lon2)
    y = wa * math.cos(lat1) * math.sin(lon1) + wb * math.cos(lat2) * math.sin(lon2)
    z = wa * math.sin(lat1) + wb * math.sin(lat2)
    return (math.degrees(math.atan2(z, math.hypot(x, y))), math.degrees(math.atan2(y, x)))


def polyline_length_km(points):
    return sum(haversine_km(*a, *b) for a, b in zip(points, points[1:]))


def sample_polyline(points, step_km):
    """Points along the polyline no more than step_km apart. The original
    vertices are kept exactly, so a route always passes through its cities."""
    if len(points) < 2:
        return list(points)
    out = []
    for a, b in zip(points, points[1:]):
        out.append(a)
        n = max(1, math.ceil(haversine_km(*a, *b) / step_km))
        out.extend(_intermediate(a, b, k / n) for k in range(1, n))
    out.append(points[-1])
    return out
