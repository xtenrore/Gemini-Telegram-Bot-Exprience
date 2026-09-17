"""Regression coverage for Plane? alert reliability guards."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.aircraft.models import NormalizedAircraft
from app.intelligence.trajectory import TrajectoryPrediction
from app.worker import reliability


def _aircraft() -> NormalizedAircraft:
    return NormalizedAircraft(
        icao24="4bb123",
        callsign="THY1017",
        latitude=41.05,
        longitude=29.02,
        altitude=3500.0,
        velocity=205.0,
        heading=180.0,
        position_age_s=1.0,
        aircraft_type="UNKNOWN",
    )


@pytest.mark.asyncio
async def test_short_primary_feed_gap_reuses_recent_aircraft(monkeypatch):
    ac = _aircraft()
    responses = [
        ([ac], {"adsb.lol": [ac]}),
        ([], {"adsb.lol": []}),
        ([], {"adsb.lol": []}),
    ]

    async def fake_query(self, **kwargs):
        return responses.pop(0)

    clock = iter([1000.0, 1010.0, 1030.0])
    monkeypatch.setattr(reliability, "_ORIGINAL_QUERY_PROVIDERS", fake_query)
    monkeypatch.setattr(reliability.time, "time", lambda: next(clock))

    manager = SimpleNamespace(opensky=SimpleNamespace(can_request_now=lambda: False))

    first, _ = await reliability._reliable_query_providers(manager, 41.0, 29.0, 80, None)
    assert [item.icao24 for item in first] == ["4bb123"]

    recovered, _ = await reliability._reliable_query_providers(manager, 41.0, 29.0, 80, None)
    assert [item.icao24 for item in recovered] == ["4bb123"]
    assert recovered[0].data_quality == "continuity-cache"
    assert recovered[0].position_age_s == pytest.approx(11.0)

    expired, _ = await reliability._reliable_query_providers(manager, 41.0, 29.0, 80, None)
    assert expired == []


@pytest.mark.asyncio
async def test_blank_primary_cycle_uses_opensky_hedge(monkeypatch):
    ac = _aircraft()
    calls: list[tuple[str, ...] | None] = []

    async def fake_query(self, **kwargs):
        names = kwargs.get("provider_names")
        calls.append(tuple(names) if names else None)
        if names == ["opensky"]:
            return [ac], {"opensky": [ac]}
        return [], {"adsb.lol": [], "adsb.fi": [], "airplanes.live": [], "adsb.one": []}

    monkeypatch.setattr(reliability, "_ORIGINAL_QUERY_PROVIDERS", fake_query)
    monkeypatch.setattr(reliability.time, "time", lambda: 2000.0)
    manager = SimpleNamespace(opensky=SimpleNamespace(can_request_now=lambda: True))

    aircraft, providers = await reliability._reliable_query_providers(manager, 41.0, 29.0, 80, None)

    assert [item.icao24 for item in aircraft] == ["4bb123"]
    assert "opensky" in providers
    assert calls == [None, ("opensky",)]


def test_direct_fresh_presence_inside_radius_recovers_late_detection():
    pred = TrajectoryPrediction(
        state="Passed",
        confidence="Uncertain",
        confidence_score=0.20,
        current_distance_km=4.0,
        current_slant_km=5.0,
        distance_trend_km_s=0.1,
        projected_closest_km=3.5,
        projected_closest_slant_km=4.5,
        time_to_cpa_s=0.0,
        radius_entry_s=None,
        enters_alert_radius=False,
        already_passed=True,
        turning_away=False,
        stale=False,
        turn_rate_deg_s=0.0,
        acceleration_kts_s=0.0,
        heading_stability_deg=2.0,
        speed_stability_kts=3.0,
        reason="closest approach is behind the current position",
        path=[],
    )

    recovered = reliability._recover_direct_presence(pred, 15.0)

    assert recovered.state == "Passing nearby"
    assert recovered.enters_alert_radius is True
    assert recovered.already_passed is False
    assert recovered.radius_entry_s == 0.0
    assert recovered.confidence_score >= 0.36


@pytest.mark.asyncio
async def test_route_network_refresh_does_not_block_alert_loop():
    blocker = asyncio.Event()

    class FakeRouteService:
        def __init__(self):
            self._route_cache = {}

        async def _historical_paths(self, key):
            return []

        async def resolve_route(self, ac):
            await blocker.wait()
            return None

    service = FakeRouteService()
    ac = SimpleNamespace(
        callsign="THY1017",
        latitude=41.0,
        longitude=29.0,
        altitude=5000.0,
        vertical_rate_mps=0.0,
        ground_speed=390.0,
    )
    pred = SimpleNamespace(
        current_distance_km=25.0,
        stale=False,
        time_to_cpa_s=180.0,
    )

    result = await asyncio.wait_for(
        reliability._nonblocking_route_evaluate(
            service,
            ac,
            pred,
            user_lat=41.1,
            user_lon=29.1,
            alert_radius_km=15.0,
            current_samples=[],
        ),
        timeout=0.05,
    )

    assert result.suppress_alert is False
    task = service._reliability_route_tasks["THY1017"]
    assert not task.done()
    blocker.set()
    await task
