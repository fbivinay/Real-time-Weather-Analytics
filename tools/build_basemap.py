"""Builds dashboard/public/india.geojson, the map's only base layer.

Natural Earth data (public domain). Country shapes come from the India
point-of-view admin-0 file, so the border is drawn as India officially
depicts it - a requirement for a map shown to Indian operations teams that
generic tile servers do not meet. State lines come from the 50 m admin-1
file. Neighbours are clipped to the region and everything is simplified
(Douglas-Peucker, ~1 km) to keep the dashboard light.

    python tools/build_basemap.py dashboard/public/india.geojson
"""
import json
import sys
import urllib.request

BASE = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/"
COUNTRIES = BASE + "ne_10m_admin_0_countries_ind.geojson"
STATES = BASE + "ne_50m_admin_1_states_provinces.geojson"
NEIGHBOURS = {"PAK", "CHN", "NPL", "BTN", "BGD", "MMR", "LKA", "AFG"}
BBOX = (60.0, 2.0, 101.0, 38.5)          # lon_min, lat_min, lon_max, lat_max
TOLERANCE = 0.01                          # degrees


def fetch(url):
    with urllib.request.urlopen(url, timeout=300) as resp:
        return json.load(resp)


def _perp(p, a, b):
    (x, y), (x1, y1), (x2, y2) = p, a, b
    dx, dy = x2 - x1, y2 - y1
    if dx == dy == 0:
        return ((x - x1) ** 2 + (y - y1) ** 2) ** 0.5
    return abs(dy * x - dx * y + x2 * y1 - y2 * x1) / (dx * dx + dy * dy) ** 0.5


def douglas_peucker(points, tol):
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        i, j = stack.pop()
        best, idx = 0.0, None
        for k in range(i + 1, j):
            d = _perp(points[k], points[i], points[j])
            if d > best:
                best, idx = d, k
        if idx is not None and best > tol:
            keep[idx] = True
            stack += [(i, idx), (idx, j)]
    return [p for p, k in zip(points, keep) if k]


def clip_ring(ring, bbox):
    """Sutherland-Hodgman against the bounding rectangle."""
    x0, y0, x1, y1 = bbox
    edges = [(lambda p: p[0] >= x0, lambda a, b: (x0, a[1] + (b[1] - a[1]) * (x0 - a[0]) / (b[0] - a[0]))),
             (lambda p: p[0] <= x1, lambda a, b: (x1, a[1] + (b[1] - a[1]) * (x1 - a[0]) / (b[0] - a[0]))),
             (lambda p: p[1] >= y0, lambda a, b: (a[0] + (b[0] - a[0]) * (y0 - a[1]) / (b[1] - a[1]), y0)),
             (lambda p: p[1] <= y1, lambda a, b: (a[0] + (b[0] - a[0]) * (y1 - a[1]) / (b[1] - a[1]), y1))]
    out = [tuple(p) for p in ring]
    for inside, cross in edges:
        if not out:
            break
        src, out = out, []
        for i, cur in enumerate(src):
            prev = src[i - 1]
            if inside(cur):
                if not inside(prev):
                    out.append(cross(prev, cur))
                out.append(cur)
            elif inside(prev):
                out.append(cross(prev, cur))
    return out


def polygons(geometry):
    if geometry["type"] == "Polygon":
        return [geometry["coordinates"]]
    if geometry["type"] == "MultiPolygon":
        return geometry["coordinates"]
    return []


def simplify(geometry, clip=False):
    result = []
    for poly in polygons(geometry):
        rings = []
        for ring in poly:
            pts = clip_ring(ring, BBOX) if clip else [tuple(p) for p in ring]
            if len(pts) < 4:
                continue
            if pts[0] != pts[-1]:
                pts.append(pts[0])
            pts = douglas_peucker(pts, TOLERANCE)
            pts = [[round(x, 3), round(y, 3)] for x, y in pts]
            if len(pts) >= 4:
                rings.append(pts)
        if rings:
            result.append(rings)
    return {"type": "MultiPolygon", "coordinates": result} if result else None


def prop(props, key):
    return props.get(key.upper()) or props.get(key.lower())


def main(out_path):
    features = []
    for f in fetch(COUNTRIES)["features"]:
        code = prop(f["properties"], "adm0_a3")
        if code == "IND" or code in NEIGHBOURS:
            geom = simplify(f["geometry"], clip=code != "IND")
            if geom:
                features.append({"type": "Feature", "geometry": geom, "properties": {
                    "layer": "country" if code == "IND" else "neighbour", "name": prop(f["properties"], "name")}})
    for f in fetch(STATES)["features"]:
        if f["properties"].get("adm0_a3") == "IND":
            geom = simplify(f["geometry"])
            if geom:
                features.append({"type": "Feature", "geometry": geom, "properties": {
                    "layer": "state", "name": f["properties"].get("name")}})
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump({"type": "FeatureCollection", "features": features}, fh, separators=(",", ":"))
    counts = {}
    for f in features:
        counts[f["properties"]["layer"]] = counts.get(f["properties"]["layer"], 0) + 1
    print(out_path, counts)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "dashboard/public/india.geojson")
