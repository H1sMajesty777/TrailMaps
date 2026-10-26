"""Pure geo helpers (no third-party dependencies)."""

import math
from collections.abc import Sequence
from datetime import date, timedelta

EARTH_RADIUS_M = 6_371_008.8

# (lng, lat) — GeoJSON / PostGIS axis order
Coordinate = Sequence[float]


def haversine_m(a: Coordinate, b: Coordinate) -> float:
    lng1, lat1 = math.radians(a[0]), math.radians(a[1])
    lng2, lat2 = math.radians(b[0]), math.radians(b[1])
    d_lat = lat2 - lat1
    d_lng = lng2 - lng1
    h = math.sin(d_lat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(d_lng / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(h)))


def path_length_m(coords: Sequence[Coordinate]) -> float:
    return sum(haversine_m(coords[i], coords[i + 1]) for i in range(len(coords) - 1))


def to_linestring_wkt(coords: Sequence[Coordinate]) -> str:
    return "LINESTRING(" + ", ".join(f"{c[0]:.7f} {c[1]:.7f}" for c in coords) + ")"


def point_ewkt(lat: float, lng: float) -> str:
    return f"SRID=4326;POINT({lng:.7f} {lat:.7f})"


def _point_segment_distance(p: Coordinate, a: Coordinate, b: Coordinate) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    if dx == 0 and dy == 0:
        return math.hypot(p[0] - a[0], p[1] - a[1])
    t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    return math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy))


def simplify_coords(coords: Sequence[Coordinate], tolerance: float) -> list[Coordinate]:
    """Iterative Ramer–Douglas–Peucker simplification (tolerance in degrees)."""
    n = len(coords)
    if n <= 2 or tolerance <= 0:
        return list(coords)
    keep = [False] * n
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        start, end = stack.pop()
        max_dist, index = 0.0, -1
        for i in range(start + 1, end):
            dist = _point_segment_distance(coords[i], coords[start], coords[end])
            if dist > max_dist:
                max_dist, index = dist, i
        if index != -1 and max_dist > tolerance:
            keep[index] = True
            stack.append((start, index))
            stack.append((index, end))
    return [c for c, k in zip(coords, keep, strict=True) if k]


def bucket_start(day: date, granularity: str) -> date:
    if granularity == "day":
        return day
    if granularity == "week":  # PostgreSQL date_trunc('week') -> Monday
        return day - timedelta(days=day.weekday())
    if granularity == "month":
        return day.replace(day=1)
    raise ValueError(f"Unknown granularity: {granularity}")


def iter_buckets(date_from: date, date_to: date, granularity: str) -> list[date]:
    """All bucket start dates covering [date_from, date_to], matching date_trunc()."""
    current = bucket_start(date_from, granularity)
    buckets: list[date] = []
    while current <= date_to:
        buckets.append(current)
        if granularity == "day":
            current += timedelta(days=1)
        elif granularity == "week":
            current += timedelta(days=7)
        else:
            current = (
                current.replace(year=current.year + 1, month=1)
                if current.month == 12
                else current.replace(month=current.month + 1)
            )
    return buckets
