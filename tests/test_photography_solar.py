from datetime import datetime, timezone

from app.photography.solar import bearing_deg, get_solar_context


def test_bearing_cardinal_directions():
    assert 80 < bearing_deg(0, 0, 0, 1) < 100
    north = bearing_deg(0, 0, 1, 0)
    assert north < 1 or north > 359


def test_solar_context_has_deterministic_geometry():
    ctx = get_solar_context(
        41.0082,
        28.9784,
        "Europe/Istanbul",
        now=datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc),
        subject_latitude=41.05,
        subject_longitude=29.10,
    )
    assert ctx.elevation_deg is not None
    assert ctx.azimuth_deg is not None
    assert 0 <= ctx.azimuth_deg < 360
    assert ctx.subject_bearing_deg is not None
    assert ctx.sun_subject_angle_deg is not None
    assert ctx.phase != "unknown"
    assert ctx.lighting_relationship != "unknown"
