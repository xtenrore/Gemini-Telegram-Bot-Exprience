from types import SimpleNamespace

import pytest

from app import telegram_map
from app.aircraft.models import NormalizedAircraft
from app.main import app
from app.worker import notifications


def test_a380_is_large_four_engine_silhouette():
    a380 = telegram_map._spec_for_type("A388")
    a320 = telegram_map._spec_for_type("A320")
    assert a380.engines == 4
    assert a380.wingspan_m > a320.wingspan_m
    assert telegram_map._icon_size_px(a380) > telegram_map._icon_size_px(a320)


def test_family_fallbacks_preserve_engine_and_aircraft_kind():
    assert telegram_map._spec_for_type("B74X").engines == 4
    assert telegram_map._spec_for_type("A34X").engines == 4
    assert telegram_map._spec_for_type("AT7X").kind == "turboprop"
    assert telegram_map._spec_for_type("GLEX").kind == "bizjet"


def test_map_caches_are_bounded():
    telegram_map._tile_cache.clear()
    for index in range(telegram_map._TILE_CACHE_MAX + 20):
        telegram_map._cache_tile((8, index, 100), b"x")
    assert len(telegram_map._tile_cache) == telegram_map._TILE_CACHE_MAX

    telegram_map._tracks.clear()
    for index in range(telegram_map._TRACK_CACHE_MAX + 20):
        telegram_map._track_for(f"icao{index}", 41.0, 29.0 + index / 100000.0)
    assert len(telegram_map._tracks) == telegram_map._TRACK_CACHE_MAX


@pytest.mark.asyncio
async def test_renderer_returns_jpeg_even_when_satellite_tiles_are_temporarily_unavailable(monkeypatch):
    async def no_tile(*_args, **_kwargs):
        return None

    monkeypatch.setattr(telegram_map, "_get_tile", no_tile)
    aircraft = NormalizedAircraft(
        icao24="4baa01",
        callsign="THY1",
        aircraft_type="A388",
        latitude=41.10,
        longitude=29.05,
        altitude=3500.0,
        velocity=200.0,
        heading=92.0,
    )
    prediction = SimpleNamespace(path=[SimpleNamespace(latitude=41.10, longitude=29.05), SimpleNamespace(latitude=41.10, longitude=29.15)])
    rendered = await telegram_map.render_telegram_map(41.08, 29.01, aircraft, prediction)
    assert rendered[:2] == b"\xff\xd8"
    assert len(rendered) > 1000


def test_alert_caption_contains_no_browser_map_link():
    aircraft = NormalizedAircraft(icao24="4baa01", callsign="THY1", aircraft_type="A320")
    pred = SimpleNamespace(
        state="Approaching",
        confidence="High",
        projected_closest_km=4.0,
        current_distance_km=20.0,
        time_to_cpa_s=180.0,
    )
    text = notifications._approach_text(aircraft, pred, "prepare", "abcdef123456")
    assert "http://" not in text
    assert "https://" not in text
    assert "inside this Telegram message" in text


def test_browser_live_map_routes_are_removed():
    paths = {getattr(route, "path", "") for route in app.routes}
    assert not any(path.startswith("/live/") for path in paths)
    assert not any(path.startswith("/api/live/") for path in paths)
