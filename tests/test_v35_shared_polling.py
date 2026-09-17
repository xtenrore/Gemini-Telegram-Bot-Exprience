"""Plane? v3.5 shared-polling regression tests."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.aircraft.models import NormalizedAircraft
from app.worker import v35


def _user(uid: int, lat: float, lon: float, radius_km: float = 15.0) -> dict:
    return {
        "user_id": uid,
        "location": {
            "latitude": lat,
            "longitude": lon,
            "radius_km": radius_km,
            # Deliberately allow different legacy geohashes: v3.5 must not use
            # cell boundaries as a provider-request boundary.
            "geohash": f"legacy-{uid}",
        },
        "preferences": {},
    }


def _aircraft(lat: float = 41.02, lon: float = 29.01) -> NormalizedAircraft:
    return NormalizedAircraft(
        icao24="4bb123",
        callsign="THY1017",
        latitude=lat,
        longitude=lon,
        altitude=3500.0,
        velocity=205.0,
        heading=180.0,
        position_age_s=1.0,
        aircraft_type="A321",
    )


def test_nearby_users_share_one_region_even_across_legacy_geohashes():
    users = [_user(i, 41.0 + (i % 10) * 0.002, 29.0 + (i // 10) * 0.002) for i in range(1, 101)]

    regions = v35.build_shared_regions(users)

    assert len(regions) == 1
    assert len(regions[0].users) == 100
    assert regions[0].radius_nm <= 250


def test_distant_users_are_split_into_safe_provider_regions():
    users = [
        _user(1, 41.0082, 28.9784),  # Istanbul
        _user(2, 51.5074, -0.1278),  # London
    ]

    regions = v35.build_shared_regions(users)

    assert len(regions) == 2
    assert all(region.radius_nm <= 250 for region in regions)


def test_region_key_changes_when_saved_location_moves():
    before = v35.build_shared_regions([_user(1, 41.0, 29.0)])[0]
    after = v35.build_shared_regions([_user(1, 41.25, 29.0)])[0]

    assert before.key != after.key


@pytest.mark.asyncio
async def test_successful_region_poll_uses_one_public_provider_and_cache(monkeypatch):
    calls: list[tuple[str, ...]] = []
    ac = _aircraft()

    class FakeManager:
        opensky = SimpleNamespace(can_request_now=lambda: False)

        async def query_providers(self, **kwargs):
            names = tuple(kwargs.get("provider_names") or ())
            calls.append(names)
            return [ac], {names[0]: [ac]}

    monkeypatch.setattr(v35.monitor, "_provider_manager", FakeManager())
    poller = v35.SharedRegionPoller()
    region = v35.build_shared_regions([_user(1, 41.0, 29.0)])[0]

    first = await poller.poll(region, cycle_number=0, stagger_new_regions=False)
    second = await poller.poll(region, cycle_number=1, stagger_new_regions=False)

    assert first.fresh is True
    assert first.provider_queries == 1
    assert len(calls) == 1
    assert calls[0][0] in v35.PUBLIC_PROVIDERS
    assert second.cache_hit is True
    assert second.provider_queries == 0


@pytest.mark.asyncio
async def test_quiet_region_reuses_snapshot_for_discovery_interval(monkeypatch):
    calls = 0
    # Far enough from the user to keep the region in 15-second discovery mode.
    ac = _aircraft(lat=42.0, lon=29.0)

    class FakeManager:
        opensky = SimpleNamespace(can_request_now=lambda: False)

        async def query_providers(self, **kwargs):
            nonlocal calls
            calls += 1
            name = (kwargs.get("provider_names") or ["unknown"])[0]
            return [ac], {name: [ac]}

    monkeypatch.setattr(v35.monitor, "_provider_manager", FakeManager())
    poller = v35.SharedRegionPoller()
    region = v35.build_shared_regions([_user(1, 41.0, 29.0)])[0]

    first = await poller.poll(region, cycle_number=0, stagger_new_regions=False)
    poller._snapshots[region.key].fetched_mono -= 6.0
    second = await poller.poll(region, cycle_number=1, stagger_new_regions=False)

    assert first.fresh is True
    assert second.cache_hit is True
    assert second.provider_queries == 0
    assert calls == 1


@pytest.mark.asyncio
async def test_hot_region_returns_to_five_second_provider_polling(monkeypatch):
    calls = 0
    ac = _aircraft(lat=41.01, lon=29.0)

    class FakeManager:
        opensky = SimpleNamespace(can_request_now=lambda: False)

        async def query_providers(self, **kwargs):
            nonlocal calls
            calls += 1
            name = (kwargs.get("provider_names") or ["unknown"])[0]
            return [ac], {name: [ac]}

    monkeypatch.setattr(v35.monitor, "_provider_manager", FakeManager())
    poller = v35.SharedRegionPoller()
    region = v35.build_shared_regions([_user(1, 41.0, 29.0)])[0]

    first = await poller.poll(region, cycle_number=0, stagger_new_regions=False)
    poller._snapshots[region.key].fetched_mono -= 6.0
    second = await poller.poll(region, cycle_number=1, stagger_new_regions=False)

    assert first.fresh is True
    assert second.fresh is True
    assert second.cache_hit is False
    assert calls == 2


@pytest.mark.asyncio
async def test_blank_primary_uses_fallback_instead_of_querying_every_provider(monkeypatch):
    calls: list[str] = []
    ac = _aircraft()

    class FakeManager:
        opensky = SimpleNamespace(can_request_now=lambda: False)

        async def query_providers(self, **kwargs):
            name = (kwargs.get("provider_names") or ["unknown"])[0]
            calls.append(name)
            if len(calls) == 1:
                return [], {name: []}
            return [ac], {name: [ac]}

    monkeypatch.setattr(v35.monitor, "_provider_manager", FakeManager())
    poller = v35.SharedRegionPoller()
    region = v35.build_shared_regions([_user(1, 41.0, 29.0)])[0]

    result = await poller.poll(region, cycle_number=0, stagger_new_regions=False)

    assert result.aircraft
    assert result.provider_queries == 2
    assert len(calls) == 2
    assert calls[0] != calls[1]
    assert all(name in v35.PUBLIC_PROVIDERS for name in calls)


@pytest.mark.asyncio
async def test_empty_region_is_cached_instead_of_retrying_each_five_second_tick(monkeypatch):
    calls = 0

    class FakeManager:
        opensky = SimpleNamespace(can_request_now=lambda: False)

        async def query_providers(self, **kwargs):
            nonlocal calls
            calls += 1
            name = (kwargs.get("provider_names") or ["unknown"])[0]
            return [], {name: []}

    monkeypatch.setattr(v35.monitor, "_provider_manager", FakeManager())
    poller = v35.SharedRegionPoller()
    region = v35.build_shared_regions([_user(1, 41.0, 29.0)])[0]

    first = await poller.poll(region, cycle_number=0, stagger_new_regions=False)
    second = await poller.poll(region, cycle_number=1, stagger_new_regions=False)

    assert first.fresh is True
    assert first.provider_queries == 2
    assert second.cache_hit is True
    assert second.provider_queries == 0
    assert calls == 2
