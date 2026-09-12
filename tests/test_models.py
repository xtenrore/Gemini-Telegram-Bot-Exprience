"""Tests for aircraft data models."""

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
