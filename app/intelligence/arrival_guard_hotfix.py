"""Production guard for destination-near-observer ETA false positives.

Keeps deterministic CPA authoritative for real observed presence, but prevents a
straight-line projection from advertising an arrival as approaching the observer
when the resolved destination geometry proves a turn must happen first.
"""
from __future__ import annotations

from dataclasses import replace

from app.intelligence import route_guard
from app.intelligence.trajectory import bearing_deg, haversine_km

_original = route_guard.evaluate_route_gate_destination_aware


def _destination_aware_arrival_guard(**kwargs):
    destination = kwargs.get("destination")
    route_plausible = bool(kwargs.get("route_plausible"))
    aircraft_lat = kwargs.get("aircraft_lat")
    aircraft_lon = kwargs.get("aircraft_lon")
    altitude_m = kwargs.get("altitude_m")
    vertical_rate_mps = kwargs.get("vertical_rate_mps")
    time_to_cpa_s = kwargs.get("time_to_cpa_s")
    projected_path = kwargs.get("projected_path")
    heading_deg = kwargs.get("heading_deg")
    observer_lat = kwargs.get("observer_lat")
    observer_lon = kwargs.get("observer_lon")
    alert_radius_km = float(kwargs.get("alert_radius_km") or 0.0)

    result = _original(**kwargs)
    if result.suppress_alert:
        return result
    if (
        not route_plausible
        or destination is None
        or getattr(destination, "latitude", None) is None
        or getattr(destination, "longitude", None) is None
        or aircraft_lat is None
        or aircraft_lon is None
        or observer_lat is None
        or observer_lon is None
        or time_to_cpa_s is None
        or float(time_to_cpa_s) <= 20.0
    ):
        return result

    current_observer = haversine_km(
        float(aircraft_lat), float(aircraft_lon), float(observer_lat), float(observer_lon)
    )
    # Physical presence inside the user's requested radius always wins.
    if current_observer <= alert_radius_km:
        return result

    destination_distance = haversine_km(
        float(aircraft_lat), float(aircraft_lon),
        float(destination.latitude), float(destination.longitude),
    )
    observer_destination = haversine_km(
        float(observer_lat), float(observer_lon),
        float(destination.latitude), float(destination.longitude),
    )
    destination_margin = alert_radius_km + max(8.0, alert_radius_km * 0.35)
    if observer_destination > destination_margin:
        return result

    terminal_phase = destination_distance <= 160.0 and (
        (altitude_m is not None and float(altitude_m) <= 8000.0)
        or (vertical_rate_mps is not None and float(vertical_rate_mps) <= -0.25)
    )

    # A resolved airport close to the observer is not, by itself, enough to
    # suppress a real overflight. Before terminal phase require geometric proof:
    # the naive straight-line CPA would carry the aircraft materially farther
    # from its known destination, and the current heading conflicts with the
    # destination bearing. This catches arrivals that must turn before reaching
    # the observer without hiding aircraft genuinely tracking through the radius.
    projected_conflict = False
    if projected_path is not None:
        cpa_destination = route_guard._projected_cpa_destination_distance(
            projected_path,
            observer_lat=float(observer_lat),
            observer_lon=float(observer_lon),
            destination=destination,
        )
        if cpa_destination is not None:
            required_growth = max(3.5, min(10.0, destination_distance * 0.10))
            heading_conflict = True
            if heading_deg is not None:
                destination_bearing = bearing_deg(
                    float(aircraft_lat), float(aircraft_lon),
                    float(destination.latitude), float(destination.longitude),
                )
                heading_conflict = abs(
                    route_guard._angle_delta(float(heading_deg), destination_bearing)
                ) >= 18.0
            projected_conflict = (
                cpa_destination - destination_distance >= required_growth
                and heading_conflict
            )

    if not terminal_phase and not projected_conflict:
        return result

    reason = (
        f"known destination {destination.code} is within observer range; "
        "terminal arrival makes straight-line observer CPA unreliable"
        if terminal_phase
        else (
            f"known destination {destination.code} requires a turn before observer CPA; "
            "straight-line projection moves away from the destination"
        )
    )
    return replace(
        result,
        suppress_alert=True,
        reason=reason,
        destination_code=destination.code,
        destination_distance_km=destination_distance,
        expected_turn_pending=True,
        route_plausible=True,
    )


route_guard.evaluate_route_gate_destination_aware = _destination_aware_arrival_guard
