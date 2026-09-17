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


def test_multi_key_secret_names_are_loaded(monkeypatch):
    monkeypatch.setenv("OPENSKY_1", "id1:secret1")
    monkeypatch.setenv("OPENSKY_5", "id5:secret5")
    monkeypatch.setenv("GEMINI_API_KEY_2", "gemini-two")
    monkeypatch.setenv("GROQ_KEY", "groq-one")
    monkeypatch.setenv("GROQ_KEY_2", "groq-two")

    s = Settings()
    assert s.opensky_1 == "id1:secret1"
    assert s.opensky_5 == "id5:secret5"
    assert s.gemini_api_key_2 == "gemini-two"
    assert s.groq_key == "groq-one"
    assert s.groq_key_2 == "groq-two"
