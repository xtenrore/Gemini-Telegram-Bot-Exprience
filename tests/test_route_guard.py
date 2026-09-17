from types import SimpleNamespace

from app.intelligence.route_guard import (
    _route_from_adsbdb,
    evaluate_route_gate_destination_aware,
)
from app.intelligence.route_history import AirportInfo


IST = AirportInfo(
    icao="LTFM",
    iata="IST",
    name="Istanbul Airport",
    latitude=41.2753,
    longitude=28.7519,
)


def point(lat, lon, horizontal_km):
    return SimpleNamespace(
        latitude=lat,
        longitude=lon,
        horizontal_km=horizontal_km,
    )


def test_istanbul_arrival_turn_vetoes_straight_line_false_positive():
    # Regression geometry from the MNS502 class of false alert: the aircraft is
    # south of IST, flying east toward the observer. A naive CPA says it will
    # pass the observer, but continuing to that CPA would move it farther from
    # its known landing airport. The arrival therefore requires a turn first.
    projected = [
        point(40.990, 28.750, 31.8),
        point(41.000, 28.900, 19.7),
        point(41.020, 29.050, 8.4),
        point(41.040, 29.100, 4.5),
    ]
    result = evaluate_route_gate_destination_aware(
        callsign="MNS502",
        current_path=[(40.990, 28.750), (40.995, 28.800), (41.000, 28.850)],
        historical_paths=[],
        observer_lat=41.080,
        observer_lon=29.110,
        alert_radius_km=15.0,
        destination=IST,
        route_plausible=True,
        aircraft_lat=40.990,
        aircraft_lon=28.750,
        altitude_m=4300.0,
        vertical_rate_mps=-5.0,
        speed_kts=285.0,
        time_to_cpa_s=120.0,
        projected_path=projected,
        heading_deg=88.0,
    )
    assert result.suppress_alert
    assert result.expected_turn_pending
    assert result.destination_code == "IST"
    assert "terminal turn" in result.reason


def test_real_path_toward_destination_and_observer_is_not_vetoed():
    projected = [
        point(40.990, 28.752, 15.0),
        point(41.040, 28.752, 9.0),
        point(41.100, 28.752, 2.0),
    ]
    result = evaluate_route_gate_destination_aware(
        callsign="TEST123",
        current_path=[(40.990, 28.752), (41.010, 28.752), (41.030, 28.752)],
        historical_paths=[],
        observer_lat=41.100,
        observer_lon=28.752,
        alert_radius_km=8.0,
        destination=IST,
        route_plausible=True,
        aircraft_lat=40.990,
        aircraft_lon=28.752,
        altitude_m=4200.0,
        vertical_rate_mps=-4.0,
        speed_kts=260.0,
        time_to_cpa_s=90.0,
        projected_path=projected,
        heading_deg=0.0,
    )
    assert not result.suppress_alert


def test_direct_presence_inside_radius_is_never_hidden_by_destination_gate():
    projected = [
        point(41.050, 29.080, 4.0),
        point(41.040, 29.100, 3.0),
    ]
    result = evaluate_route_gate_destination_aware(
        callsign="MNS502",
        current_path=[(41.050, 29.080), (41.045, 29.090)],
        historical_paths=[],
        observer_lat=41.080,
        observer_lon=29.110,
        alert_radius_km=15.0,
        destination=IST,
        route_plausible=True,
        aircraft_lat=41.050,
        aircraft_lon=29.080,
        altitude_m=3000.0,
        vertical_rate_mps=-5.0,
        speed_kts=220.0,
        time_to_cpa_s=40.0,
        projected_path=projected,
        heading_deg=90.0,
    )
    assert not result.suppress_alert


def test_adsbdb_callsign_fallback_parses_mji_to_ist_and_validates_position():
    payload = {
        "response": {
            "flightroute": {
                "callsign": "MNS502",
                "callsign_icao": "MNS502",
                "callsign_iata": "BM502",
                "origin": {
                    "icao_code": "HLLM",
                    "iata_code": "MJI",
                    "name": "Mitiga International Airport",
                    "latitude": 32.8941,
                    "longitude": 13.2760,
                },
                "destination": {
                    "icao_code": "LTFM",
                    "iata_code": "IST",
                    "name": "Istanbul Airport",
                    "latitude": 41.2753,
                    "longitude": 28.7519,
                },
            }
        }
    }
    route = _route_from_adsbdb(
        "MNS502",
        payload,
        latitude=40.990,
        longitude=28.750,
    )
    assert route is not None
    assert route.plausible
    assert route.origin is not None and route.origin.iata == "MJI"
    assert route.destination is not None and route.destination.iata == "IST"
