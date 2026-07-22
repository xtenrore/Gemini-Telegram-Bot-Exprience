"""Geospatial utility functions.

Provides Haversine distance, bounding-box calculation, and geohash helpers
for clustering users into spatial regions.
"""

from __future__ import annotations

import math

import geohash2  # type: ignore[import-untyped]

# Earth's mean radius in kilometres
_EARTH_RADIUS_KM = 6371.0


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return the great-circle distance in **kilometres** between two points.

    Uses the Haversine formula.
    """
    lat1_r, lon1_r = math.radians(lat1), math.radians(lon1)
    lat2_r, lon2_r = math.radians(lat2), math.radians(lon2)

    dlat = lat2_r - lat1_r
    dlon = lon2_r - lon1_r

    a = math.sin(dlat / 2) ** 2 + math.cos(lat1_r) * math.cos(lat2_r) * math.sin(dlon / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return _EARTH_RADIUS_KM * c


def is_within_square_and_circle(
    user_lat: float, user_lon: float, ac_lat: float, ac_lon: float, radius_km: float
) -> tuple[bool, float]:
    """Check if point is inside square bounding box (+/- radius_km) and within distance limit.

    Returns (is_inside, circular_distance_km).
    """
    # Latitude difference in km (1° lat ≈ 111.32 km)
    lat_diff_km = abs(ac_lat - user_lat) * 111.32
    if lat_diff_km > radius_km:
        return False, haversine(user_lat, user_lon, ac_lat, ac_lon)

    # Longitude difference in km (varies with latitude)
    cos_lat = math.cos(math.radians(user_lat))
    lon_diff_km = abs(ac_lon - user_lon) * (111.32 * cos_lat) if cos_lat > 0 else 0.0
    if lon_diff_km > radius_km:
        return False, haversine(user_lat, user_lon, ac_lat, ac_lon)

    distance = haversine(user_lat, user_lon, ac_lat, ac_lon)
    return distance <= radius_km, distance


def bounding_box(
    lat: float, lon: float, radius_km: float
) -> tuple[float, float, float, float]:
    """Return a rectangular bounding box enclosing a circle.

    Returns ``(min_lat, max_lat, min_lon, max_lon)`` in degrees.
    """
    # Latitude offset (1° latitude ≈ 111.32 km)
    lat_offset = radius_km / 111.32

    # Longitude offset varies with latitude
    lon_offset = radius_km / (111.32 * math.cos(math.radians(lat)))

    return (
        lat - lat_offset,
        lat + lat_offset,
        lon - lon_offset,
        lon + lon_offset,
    )


def merge_bounding_boxes(
    boxes: list[tuple[float, float, float, float]],
) -> tuple[float, float, float, float]:
    """Merge multiple bounding boxes into a single enclosing box.

    Each box is ``(min_lat, max_lat, min_lon, max_lon)``.
    """
    if not boxes:
        raise ValueError("Cannot merge an empty list of bounding boxes.")

    min_lat = min(b[0] for b in boxes)
    max_lat = max(b[1] for b in boxes)
    min_lon = min(b[2] for b in boxes)
    max_lon = max(b[3] for b in boxes)

    return (min_lat, max_lat, min_lon, max_lon)


def compute_geohash(lat: float, lon: float, precision: int = 4) -> str:
    """Compute a geohash string for the given coordinates.

    Precision 4 gives ~20 km × 20 km cells – good for grouping nearby users
    so a single API call can serve many users in the same region.
    """
    return geohash2.encode(lat, lon, precision=precision)


def km_to_nautical_miles(km: float) -> float:
    """Convert kilometres to nautical miles."""
    return km / 1.852


def metres_to_feet(metres: float) -> int:
    """Convert metres to feet (rounded to nearest integer)."""
    return round(metres / 0.3048)


def ms_to_knots(ms: float) -> int:
    """Convert m/s to knots (rounded to nearest integer)."""
    return round(ms / 0.514444)


def heading_to_cardinal(heading: float | None) -> str:
    """Convert a heading in degrees to a cardinal/intercardinal direction."""
    if heading is None:
        return "N/A"
    directions = [
        "N", "NNE", "NE", "ENE",
        "E", "ESE", "SE", "SSE",
        "S", "SSW", "SW", "WSW",
        "W", "WNW", "NW", "NNW",
    ]
    idx = round(heading / 22.5) % 16
    return directions[idx]
