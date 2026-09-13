"""Tests for aircraft data models."""

import pytest

from app.aircraft.models import NormalizedAircraft


def test_normalized_aircraft_creation():
    """Verify aircraft creation with full fields."""
    ac = NormalizedAircraft(
        icao24="a1234b",
        callsign="UAL123",
        origin_country="United States",
        latitude=37.7749,
        longitude=-122.4194,
        altitude=10000.0,
        velocity=230.0,
        heading=180.0,
        aircraft_type="B738",
    )
    assert ac.icao24 == "a1234b"
    assert ac.has_position is True
    assert ac.display_type == "B738"


def test_normalized_aircraft_defaults_and_properties():
    """Verify behavior with missing position and unknown type."""
    ac = NormalizedAircraft(icao24="deadbeef")
    assert ac.has_position is False
    assert ac.display_type == "Unknown"
    assert ac.callsign == ""
    assert ac.origin_country == ""
    assert ac.ground_speed is None
    assert ac.track is None
    assert ac.turn_rate == 0.0


def test_ground_speed_and_track_compatibility_for_kinematics():
    """Provider m/s data must reach the trajectory engine as knots."""
    ac = NormalizedAircraft(
        icao24="abc123",
        velocity=100.0,
        heading=271.5,
    )
    assert ac.ground_speed == pytest.approx(194.384449, rel=1e-6)
    assert ac.speed == pytest.approx(ac.ground_speed)
    assert ac.track == 271.5
