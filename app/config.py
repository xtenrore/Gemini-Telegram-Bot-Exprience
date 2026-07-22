"""Application configuration loaded from environment variables / .env file."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All bot configuration, loaded from environment variables or a .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Telegram ────────────────────────────────────────────────────────
    telegram_bot_token: str = ""

    # ── MongoDB ─────────────────────────────────────────────────────────
    mongo_uri: str = "mongodb://localhost:27017"
    database_name: str = "aircraft_bot"

    # ── Aircraft Data Providers ─────────────────────────────────────────
    adsb_lol_base_url: str = "https://api.adsb.lol/v2"
    adsb_fi_base_url: str = "https://api.adsb.fi/v2"
    opensky_base_url: str = "https://opensky-network.org/api"
    airplanes_live_base_url: str = "https://api.airplanes.live/v2"
    adsb_one_base_url: str = "https://api.adsb.one/v2"

    # OpenSky OAuth2 token endpoint
    opensky_token_url: str = (
        "https://auth.opensky-network.org/auth/realms/"
        "opensky-network/protocol/openid-connect/token"
    )

    # OpenSky API keys directory (contains .json credential files)
    api_keys_dir: str = "api"

    # ── AI Providers ───────────────────────────────────────────────────
    gemini_api_key: str = ""
    gemini_model_primary: str = "gemini-2.0-flash-lite"
    gemini_model_secondary: str = "gemini-2.0-flash-lite"
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"

    # ── Monitoring ──────────────────────────────────────────────────────
    poll_interval_seconds: int = 10
    default_radius_km: float = 50.0
    cooldown_minutes: int = 30

    # ── Learning ────────────────────────────────────────────────────────
    learning_plane_threshold: int = 100  # planes to observe before selecting providers
    relearn_plane_count: int = 25  # extra planes on dislike feedback

    # ── Admin ───────────────────────────────────────────────────────────
    admin_telegram_id: int | None = None
    admin_password: str = ""  # Optional password for admin panel

    # ── Server ──────────────────────────────────────────────────────────
    log_level: str = "INFO"


# Singleton – import this everywhere
settings = Settings()
