from app.intelligence.route_history import AirportInfo, evaluate_route_gate, normalize_flight_key, route_similarity_km


def path(lon_offset=0.0):
    return [(41.30, 28.70 + lon_offset), (41.24, 28.78 + lon_offset), (41.18, 28.86 + lon_offset), (41.12, 28.94 + lon_offset), (41.08, 29.02 + lon_offset)]


def test_history_is_keyed_by_flight_number_callsign_not_registration_style():
    assert normalize_flight_key("THY1017") == "THY1017"
    assert normalize_flight_key("TK1017") == "TK1017"
    assert normalize_flight_key("TC-JPL") == ""


def test_route_similarity_is_small_for_same_path_and_large_for_divergent_path():
    assert route_similarity_km(path(), path(0.005)) < 1.0
    assert route_similarity_km(path(), path(0.20)) > 10.0


def test_matching_recent_routes_that_stay_outside_radius_veto_alert():
    user = (41.00, 29.00)
    historic = [path(-0.25), path(-0.245), path(-0.255)]
    current = historic[0][:4]
    result = evaluate_route_gate(
        callsign="THY1017",
        current_path=current,
        historical_paths=historic,
        observer_lat=user[0],
        observer_lon=user[1],
        alert_radius_km=10.0,
    )
    assert result.suppress_alert
    assert result.history_days == 3
    assert "outside" in result.reason


def test_inconsistent_last_three_routes_veto_alert_instead_of_guessing():
    result = evaluate_route_gate(
        callsign="THY1017",
        current_path=path()[:4],
        historical_paths=[path(), path(0.02), path(0.25)],
        observer_lat=41.0,
        observer_lon=29.0,
        alert_radius_km=15.0,
    )
    assert result.suppress_alert
    assert "inconsistent" in result.reason


def test_known_landing_before_cpa_vetoes_even_without_history():
    result = evaluate_route_gate(
        callsign="THY1017",
        current_path=path()[:3],
        historical_paths=[],
        observer_lat=41.0,
        observer_lon=29.0,
        alert_radius_km=15.0,
        destination=AirportInfo(icao="LTFM", iata="IST", latitude=41.2753, longitude=28.7519),
        route_plausible=True,
        aircraft_lat=41.50,
        aircraft_lon=28.75,
        altitude_m=4200.0,
        vertical_rate_mps=-7.0,
        speed_kts=300.0,
        time_to_cpa_s=420.0,
    )
    assert result.suppress_alert
    assert result.expected_turn_pending
    assert result.destination_code == "IST"
