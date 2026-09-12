"""Tests for geospatial utilities."""

import math
from app.worker.geo import (
    bounding_box,
    compute_geohash,
    haversine,
    heading_to_cardinal,
    is_within_square_and_circle,
    km_to_nautical_miles,
    merge_bounding_boxes,
    metres_to_feet,
    ms_to_knots,
)


def test_haversine_same_point():
    """Distance between same point should be 0."""
    dist = haversine(40.7128, -74.0060, 40.7128, -74.0060)
    assert dist == 0.0


def test_haversine_known_distance():
    """Verify London to Paris distance (~340 km)."""
    dist = haversine(51.5074, -0.1278, 48.8566, 2.3522)
    assert 330.0 < dist < 355.0


def test_is_within_square_and_circle():
    """Check spatial containment logic."""
    user_lat, user_lon = 51.5074, -0.1278
    # Point very close (~1 km)
    inside, dist = is_within_square_and_circle(user_lat, user_lon, 51.515, -0.1278, 15.0)
    assert inside is True
    assert dist < 5.0

    # Point far away (>50 km)
    outside, dist_far = is_within_square_and_circle(user_lat, user_lon, 52.5074, -0.1278, 15.0)
    assert outside is False
    assert dist_far > 15.0


def test_bounding_box_and_merge():
    """Verify bounding box generation and merge."""
    b1 = bounding_box(50.0, 10.0, 20.0)
    b2 = bounding_box(51.0, 11.0, 20.0)

    merged = merge_bounding_boxes([b1, b2])
    assert merged[0] <= min(b1[0], b2[0])
    assert merged[1] >= max(b1[1], b2[1])
    assert merged[2] <= min(b1[2], b2[2])
    assert merged[3] >= max(b1[3], b2[3])


def test_compute_geohash():
    """Verify geohash generation."""
    gh = compute_geohash(51.5074, -0.1278, precision=4)
    assert isinstance(gh, str)
    assert len(gh) == 4


def test_unit_converters():
    """Verify conversions."""
    assert km_to_nautical_miles(1.852) == 1.0
    assert metres_to_feet(1000) == 3281
    assert ms_to_knots(10.0) == 19
    assert heading_to_cardinal(0) == "N"
    assert heading_to_cardinal(90) == "E"
    assert heading_to_cardinal(180) == "S"
    assert heading_to_cardinal(270) == "W"
    assert heading_to_cardinal(None) == "N/A"
