from types import SimpleNamespace

from app import live_map
from app.aircraft.models import NormalizedAircraft
from app.worker import notifications


def test_live_map_tokens_are_narrow_and_share_safe():
    assert live_map._valid_token("a1b2c3d4e5f6")
    assert live_map._valid_token("abc_DEF-123")
    assert not live_map._valid_token("short")
    assert not live_map._valid_token("../../etc/passwd")
    assert not live_map._valid_token("abc def ghi")


def test_live_map_session_cache_is_bounded():
    live_map._sessions.clear()
    for index in range(live_map._MAX_SESSIONS + 20):
        live_map._cache_put(f"token{index:06d}", {"index": index})
    assert len(live_map._sessions) == live_map._MAX_SESSIONS
    assert f"token{0:06d}" not in live_map._sessions
    live_map._sessions.clear()


def test_history_payload_uses_existing_monitor_samples(monkeypatch):
    samples = [
        SimpleNamespace(
            timestamp=1_800_000_000.0,
            latitude=41.0,
            longitude=29.0,
            altitude_m=3500.0,
            speed_kts=390.0,
            heading_deg=90.0,
            position_age_s=1.0,
        )
    ]
    monkeypatch.setattr(live_map.time, "time", lambda: 1_800_000_001.0)
    monkeypatch.setattr(live_map, "get_aircraft_history", lambda _icao: samples)
    payload = live_map._history_payload("4baa01")
    assert payload == [
        {
            "latitude": 41.0,
            "longitude": 29.0,
            "altitude_m": 3500.0,
            "speed_kts": 390.0,
            "heading_deg": 90.0,
            "position_age_s": 1.0,
            "sample_epoch_ms": 1_800_000_000_000,
        }
    ]


def test_notification_prefers_plane_live_map_on_railway(monkeypatch):
    monkeypatch.setattr(notifications.settings, "webhook_url", "")
    monkeypatch.setenv("RAILWAY_PUBLIC_DOMAIN", "plane.example.test")
    aircraft = NormalizedAircraft(icao24="4baa01", aircraft_type="A320")
    rendered = notifications._map_link(aircraft, "abcdef123456")
    assert "https://plane.example.test/live/abcdef123456" in rendered
    assert "Live relative map" in rendered
    assert "globe.adsb.fi/?icao=4baa01" in rendered
