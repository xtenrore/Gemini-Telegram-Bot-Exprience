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

    # OpenSky API keys directory (contains .json credential files)
    api_keys_dir: str = "api"

    # ── Monitoring ──────────────────────────────────────────────────────
    poll_interval_seconds: int = 45
    default_radius_km: float = 50.0
    cooldown_minutes: int = 30

    # ── Admin ───────────────────────────────────────────────────────────
    admin_telegram_id: int | None = None
    admin_password: str = ""  # Optional password for admin panel

    # ── Server ──────────────────────────────────────────────────────────
    log_level: str = "INFO"


# Singleton – import this everywhere
settings = Settings()
