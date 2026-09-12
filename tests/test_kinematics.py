"""Tests for kinematic physics engine and trajectory prediction."""

from app.worker.kinematics import (
    evaluate_early_warning,
    haversine_distance_km,
    initial_bearing,
    project_point,
    simulate_trajectory,
)


def test_initial_bearing():
    """Verify cardinal bearings."""
    # North
    b_north = initial_bearing(0.0, 0.0, 1.0, 0.0)
    assert round(b_north) == 0 or round(b_north) == 360

    # East
    b_east = initial_bearing(0.0, 0.0, 0.0, 1.0)
    assert round(b_east) == 90

    # South
    b_south = initial_bearing(1.0, 0.0, 0.0, 0.0)
    assert round(b_south) == 180

    # West
    b_west = initial_bearing(0.0, 1.0, 0.0, 0.0)
    assert round(b_west) == 270


def test_project_point():
    """Projecting 111.32 km North should advance ~1 degree latitude."""
    lat, lon = project_point(0.0, 0.0, 111.32, 0.0)
    assert abs(lat - 1.0) < 0.05
    assert abs(lon - 0.0) < 0.05


def test_simulate_trajectory():
    """Trajectory simulation should generate sequential points."""
    points = simulate_trajectory(
        start_lat=50.0,
        start_lon=10.0,
        speed_kts=400.0,
        heading_deg=90.0,
        turn_rate_deg_s=0.0,
        user_lat=50.0,
        user_lon=11.0,
        lookahead_seconds=60,
        time_step_s=2,
    )
    assert len(points) == 31  # t=0, 2, 4, ..., 60
    assert points[0].seconds == 0
    assert points[-1].seconds == 60
    assert points[0].lat == 50.0


def test_evaluate_early_warning_already_inside():
    """Aircraft already inside radius should notify immediately with eta 0."""
    result = evaluate_early_warning(
        start_lat=50.0,
        start_lon=10.0,
        speed_kts=300.0,
        heading_deg=90.0,
        turn_rate_deg_s=0.0,
        user_lat=50.0,
        user_lon=10.05,
        radius_km=15.0,
    )
    assert result["should_notify"] is True
    assert result["eta_seconds"] == 0.0
    assert result["pass_type"] == "direct_hit"


def test_evaluate_early_warning_approaching():
    """Aircraft flying directly toward user should trigger early warning."""
    user_lat, user_lon = 50.0, 10.5
    # Aircraft ~20 km West, flying East (heading 90) at 450 kts (~230 m/s = ~0.8 km/s)
    # Target radius 10 km, so it will enter radius in ~15-20 seconds
    start_lat, start_lon = 50.0, 10.2
    result = evaluate_early_warning(
        start_lat=start_lat,
        start_lon=start_lon,
        speed_kts=450.0,
        heading_deg=90.0,
        turn_rate_deg_s=0.0,
        user_lat=user_lat,
        user_lon=user_lon,
        radius_km=10.0,
        lookahead_seconds=180,
    )
    assert result["should_notify"] is True
    assert result["eta_seconds"] is not None
    assert result["eta_seconds"] >= 0


def test_evaluate_early_warning_flying_away():
    """Aircraft flying away should not notify."""
    user_lat, user_lon = 50.0, 10.5
    # Aircraft at 10.2 flying West (heading 270), moving away from user
    result = evaluate_early_warning(
        start_lat=50.0,
        start_lon=10.2,
        speed_kts=450.0,
        heading_deg=270.0,
        turn_rate_deg_s=0.0,
        user_lat=user_lat,
        user_lon=user_lon,
        radius_km=10.0,
        lookahead_seconds=180,
    )
    assert result["should_notify"] is False
