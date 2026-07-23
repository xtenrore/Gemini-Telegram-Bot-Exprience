"""Kinematic Physics Engine for High-Precision Trajectory Extrapolation.

Computes Haversine spherical distance, bearing, forward point projections,
turn-rate arc geometry, Closest Distance of Approach (CDA), and ETA.
"""

from __future__ import annotations

import math
from typing import NamedTuple

# Earth's mean radius in kilometres
EARTH_RADIUS_KM = 6371.0

# Speed conversion constant: 1 knot = 0.000514444 km/s
KNOTS_TO_KM_PER_SEC = 0.0005144444444444444


class TrajectoryPoint(NamedTuple):
    """Point along a projected trajectory path."""

    seconds: int
    lat: float
    lon: float
    distance_to_user_km: float


def haversine_distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return the great-circle distance in kilometres between two coordinates."""
    lat1_r, lon1_r = math.radians(lat1), math.radians(lon1)
    lat2_r, lon2_r = math.radians(lat2), math.radians(lon2)

    dlat = lat2_r - lat1_r
    dlon = lon2_r - lon1_r

    a = math.sin(dlat / 2) ** 2 + math.cos(lat1_r) * math.cos(lat2_r) * math.sin(dlon / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return EARTH_RADIUS_KM * c


def initial_bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return initial bearing in degrees (0-360) from point 1 to point 2."""
    lat1_r, lon1_r = math.radians(lat1), math.radians(lon1)
    lat2_r, lon2_r = math.radians(lat2), math.radians(lon2)

    dlon = lon2_r - lon1_r
    x = math.sin(dlon) * math.cos(lat2_r)
    y = math.cos(lat1_r) * math.sin(lat2_r) - math.sin(lat1_r) * math.cos(lat2_r) * math.cos(dlon)

    initial_bearing_rad = math.atan2(x, y)
    initial_bearing_deg = (math.degrees(initial_bearing_rad) + 360) % 360
    return initial_bearing_deg


def project_point(lat: float, lon: float, distance_km: float, bearing_deg: float) -> tuple[float, float]:
    """Project a point from (lat, lon) along a bearing by distance_km.

    Returns (new_lat, new_lon) in degrees.
    """
    lat_r = math.radians(lat)
    lon_r = math.radians(lon)
    bearing_r = math.radians(bearing_deg)
    angular_dist = distance_km / EARTH_RADIUS_KM

    new_lat_r = math.asin(
        math.sin(lat_r) * math.cos(angular_dist)
        + math.cos(lat_r) * math.sin(angular_dist) * math.cos(bearing_r)
    )

    new_lon_r = lon_r + math.atan2(
        math.sin(bearing_r) * math.sin(angular_dist) * math.cos(lat_r),
        math.cos(angular_dist) - math.sin(lat_r) * math.sin(new_lat_r),
    )

    new_lat = math.degrees(new_lat_r)
    new_lon = (math.degrees(new_lon_r) + 540) % 360 - 180
    return new_lat, new_lon


def simulate_trajectory(
    start_lat: float,
    start_lon: float,
    speed_kts: float,
    heading_deg: float,
    turn_rate_deg_s: float,
    user_lat: float,
    user_lon: float,
    lookahead_seconds: int = 180,
    time_step_s: int = 2,
) -> list[TrajectoryPoint]:
    """Simulate aircraft trajectory path over lookahead_seconds.

    Applies turn_rate_deg_s to simulate banking curves or straight lines.
    Returns list of TrajectoryPoint entries sampled every time_step_s.
    """
    points: list[TrajectoryPoint] = []

    curr_lat = start_lat
    curr_lon = start_lon
    curr_heading = heading_deg % 360.0
    speed_km_s = speed_kts * KNOTS_TO_KM_PER_SEC

    # Initial point t = 0
    d_initial = haversine_distance_km(curr_lat, curr_lon, user_lat, user_lon)
    points.append(TrajectoryPoint(0, curr_lat, curr_lon, d_initial))

    for t in range(time_step_s, lookahead_seconds + 1, time_step_s):
        # Step distance in km
        step_dist_km = speed_km_s * time_step_s

        # Project position using current heading
        curr_lat, curr_lon = project_point(curr_lat, curr_lon, step_dist_km, curr_heading)

        # Update heading if turning
        if abs(turn_rate_deg_s) > 0.001:
            curr_heading = (curr_heading + turn_rate_deg_s * time_step_s) % 360.0

        # Calculate distance to user
        dist = haversine_distance_km(curr_lat, curr_lon, user_lat, user_lon)
        points.append(TrajectoryPoint(t, curr_lat, curr_lon, dist))

    return points


def evaluate_early_warning(
    start_lat: float,
    start_lon: float,
    speed_kts: float,
    heading_deg: float,
    turn_rate_deg_s: float,
    user_lat: float,
    user_lon: float,
    radius_km: float,
    lookahead_seconds: int = 180,
    buffer_km: float = 15.0,
) -> dict:
    """Evaluate aircraft trajectory against user target location.

    Returns dict containing:
      - should_notify: bool
      - eta_seconds: float | None
      - closest_pass_km: float
      - pass_type: "direct_hit" | "curve_intercept" | "pass_by" | "outside"
      - reason: str
    """
    initial_dist = haversine_distance_km(start_lat, start_lon, user_lat, user_lon)

    # 1. Fast rejection if aircraft is beyond outer buffer (user_radius + buffer_km)
    max_search_radius = radius_km + buffer_km
    if initial_dist > max_search_radius + (speed_kts * KNOTS_TO_KM_PER_SEC * lookahead_seconds):
        return {
            "should_notify": False,
            "eta_seconds": None,
            "closest_pass_km": round(initial_dist, 2),
            "pass_type": "outside",
            "reason": f"Aircraft is too far ({initial_dist:.1f}km) to intercept within lookahead window.",
        }

    # 2. Simulate trajectory path
    points = simulate_trajectory(
        start_lat=start_lat,
        start_lon=start_lon,
        speed_kts=speed_kts,
        heading_deg=heading_deg,
        turn_rate_deg_s=turn_rate_deg_s,
        user_lat=user_lat,
        user_lon=user_lon,
        lookahead_seconds=lookahead_seconds,
        time_step_s=2,
    )

    # Find Closest Distance of Approach (CDA)
    cda_point = min(points, key=lambda p: p.distance_to_user_km)
    closest_dist = cda_point.distance_to_user_km

    # Check if trajectory enters user notification radius
    entry_point = next((p for p in points if p.distance_to_user_km <= radius_km), None)

    # Check if turning contributed to the intercept
    is_turning = abs(turn_rate_deg_s) > 0.05

    # 3. Decision Logic
    if initial_dist <= radius_km:
        # Aircraft is already inside the user radius
        return {
            "should_notify": True,
            "eta_seconds": 0.0,
            "closest_pass_km": round(closest_dist, 2),
            "pass_type": "direct_hit",
            "reason": f"Aircraft currently inside radius ({initial_dist:.1f}km <= {radius_km:.1f}km).",
        }

    if entry_point is not None:
        pass_type = "curve_intercept" if is_turning else ("direct_hit" if closest_dist <= radius_km * 0.5 else "pass_by")
        return {
            "should_notify": True,
            "eta_seconds": float(entry_point.seconds),
            "closest_pass_km": round(closest_dist, 2),
            "pass_type": pass_type,
            "reason": f"Early warning: Predicted intercept in ~{entry_point.seconds}s (closest pass: {closest_dist:.2f}km).",
        }

    return {
        "should_notify": False,
        "eta_seconds": None,
        "closest_pass_km": round(closest_dist, 2),
        "pass_type": "outside",
        "reason": f"Aircraft trajectory will pass at {closest_dist:.2f}km (outside target radius {radius_km:.1f}km).",
    }
