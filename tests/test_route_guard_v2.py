from types import SimpleNamespace

import pytest

import app.intelligence.route_guard_v2 as guard
from app.intelligence.route_history import AirportInfo, FlightRouteInfo, RouteHistoryService


@pytest.mark.asyncio
async def test_route_evaluation_never_waits_for_network_refresh(monkeypatch):
    service = RouteHistoryService()

    async def no_history(key):
        return []

    scheduled = []
    service._historical_paths = no_history
    monkeypatch.setattr(guard, "_schedule_route_refresh", lambda _service, ac: scheduled.append(ac.callsign))

    ac = SimpleNamespace(
        callsign="THY1017",
        latitude=41.10,
        longitude=29.15,
        altitude=5000.0,
        vertical_rate_mps=-4.0,
        ground_speed=280.0,
        heading=260.0,
    )
    pred = SimpleNamespace(
        stale=False,
        current_distance_km=25.0,
        time_to_cpa_s=130.0,
        path=[],
    )
    result = await guard.evaluate_route_nonblocking(
        service,
        ac,
        pred,
        user_lat=41.0,
        user_lon=29.0,
        alert_radius_km=15.0,
        current_samples=[(41.10, 29.15), (41.09, 29.13), (41.08, 29.11)],
    )

    assert scheduled == ["THY1017"]
    assert result.callsign == "THY1017"


def test_conflicting_sources_keep_position_aware_primary_destination():
    ist = AirportInfo(icao="LTFM", iata="IST", latitude=41.2753, longitude=28.7519)
    saw = AirportInfo(icao="LTFJ", iata="SAW", latitude=40.8986, longitude=29.3092)
    primary = FlightRouteInfo("THY1017", "AAA-IST", True, destination=ist)
    fallback = FlightRouteInfo("THY1017", "AAA-SAW", True, destination=saw)

    route, source = guard._choose_route_prefer_position_aware(
        "THY1017",
        [("adsb.im", primary), ("adsbdb", fallback)],
    )

    assert route is primary
    assert route.destination is ist
    assert source == "adsb.im:conflict"
