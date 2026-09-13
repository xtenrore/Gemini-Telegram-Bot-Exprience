"""Tests for application settings and configuration."""

from app.config import Settings


def test_default_settings():
    """Verify default settings configuration."""
    s = Settings()
    assert s.poll_interval_seconds > 0
    assert s.default_radius_km > 0
    assert s.cooldown_minutes > 0
    assert s.database_name == "aircraft_bot"
    assert s.port == 8000
    assert s.host == "0.0.0.0"


def test_settings_env_override(monkeypatch):
    """Verify settings can be overridden via environment variables."""
    monkeypatch.setenv("POLL_INTERVAL_SECONDS", "10")
    monkeypatch.setenv("DEFAULT_RADIUS_KM", "25.5")
    monkeypatch.setenv("WEBHOOK_URL", "https://example.com/alerts")
    s = Settings()
    assert s.poll_interval_seconds == 10
    assert s.default_radius_km == 25.5
    assert s.webhook_url == "https://example.com/alerts"
