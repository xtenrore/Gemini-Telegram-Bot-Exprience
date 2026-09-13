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

    # ── Slack ───────────────────────────────────────────────────────────
    slack_bot_token: str = ""
    slack_app_token: str = ""
    slack_alert_channel_id: str = ""
    admin_slack_user_id: str = ""

    # ── Legacy Telegram values (ignored by the runtime) ────────────────
    # Kept temporarily so old local .env files and legacy unit tests still load.
    telegram_bot_token: str = ""
    webhook_url: str = ""
    webhook_secret: str = ""

    # ── MongoDB ─────────────────────────────────────────────────────────
    mongo_uri: str = "mongodb://localhost:27017"
    database_name: str = "aircraft_bot"

    # ── Aircraft Data Providers ─────────────────────────────────────────
    adsb_lol_base_url: str = "https://api.adsb.lol/v2"
    adsb_fi_base_url: str = "https://opendata.adsb.fi/api/v2"
    opensky_base_url: str = "https://opensky-network.org/api"
    airplanes_live_base_url: str = "https://api.airplanes.live/v2"
    adsb_one_base_url: str = "https://api.adsb.one/v2"

    opensky_token_url: str = (
        "https://auth.opensky-network.org/auth/realms/"
        "opensky-network/protocol/openid-connect/token"
    )
    opensky_credentials_json: str = ""
    api_keys_dir: str = "api"

    # ── AI Providers ────────────────────────────────────────────────────
    gemini_api_key: str = ""
    gemini_model_primary: str = "gemini-3.5-flash-lite"
    gemini_model_secondary: str = "gemini-3.1-flash-lite"
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"

    # ── Trajectory Prediction ───────────────────────────────────────────
    predictor_service_url: str = ""

    # ── Monitoring ──────────────────────────────────────────────────────
    poll_interval_seconds: int = 5
    default_radius_km: float = 15.0
    cooldown_minutes: int = 30

    # ── Learning ────────────────────────────────────────────────────────
    learning_plane_threshold: int = 100
    relearn_plane_count: int = 25

    # ── Admin ───────────────────────────────────────────────────────────
    admin_password: str = ""

    # ── Server ──────────────────────────────────────────────────────────
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"


settings = Settings()
