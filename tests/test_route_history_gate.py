from types import SimpleNamespace

import pytest

import app.intelligence.route_history as route_history
from app.intelligence.route_history import AirportInfo, RouteHistoryService, evaluate_route_gate, normalize_flight_key, route_similarity_km


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


def test_yesterdays_matching_route_is_enough_to_veto_expected_turn_false_positive():
    historic = [path(-0.25)]
    result = evaluate_route_gate(
        callsign="THY1017",
        current_path=historic[0][:4],
        historical_paths=historic,
        observer_lat=41.0,
        observer_lon=29.0,
        alert_radius_km=10.0,
    )
    assert result.suppress_alert
    assert result.history_days == 1
    assert result.similar_days == 1


def test_today_diverging_from_yesterdays_route_is_suppressed():
    result = evaluate_route_gate(
        callsign="THY1017",
        current_path=path(0.25)[:4],
        historical_paths=[path()],
        observer_lat=41.0,
        observer_lon=29.0,
        alert_radius_km=10.0,
    )
    assert result.suppress_alert
    assert "diverges" in result.reason


class FakeResponse:
    def __init__(self, payload=None, *, json_error=False, status_code=200):
        self.payload = payload
        self.json_error = json_error
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        if self.json_error:
            raise ValueError("not json")
        return self.payload


class FakeRouteClient:
    def __init__(self):
        self.single_called = False

    async def post(self, *args, **kwargs):
        return FakeResponse(json_error=True)

    async def get(self, url, *args, **kwargs):
        self.single_called = True
        assert "/THY1017/" in url
        return FakeResponse({
            "callsign": "THY1017",
            "airport_codes": "EGLL-LTFM",
            "plausible": True,
            "_airports": [
                {"icao": "EGLL", "iata": "LHR", "name": "Heathrow", "lat": 51.4700, "lon": -0.4543},
                {"icao": "LTFM", "iata": "IST", "name": "Istanbul Airport", "lat": 41.2753, "lon": 28.7519},
            ],
        })


@pytest.mark.asyncio
async def test_route_lookup_falls_back_to_single_endpoint_when_bulk_is_not_json(monkeypatch):
    client = FakeRouteClient()

    async def fake_get_http_client():
        return client

    monkeypatch.setattr(route_history, "get_http_client", fake_get_http_client)
    service = RouteHistoryService()
    ac = SimpleNamespace(callsign="THY1017", latitude=41.5, longitude=28.75)
    resolved = await service.resolve_route(ac)

    assert client.single_called
    assert resolved is not None
    assert resolved.plausible is True
    assert resolved.destination is not None
    assert resolved.destination.iata == "IST"
