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

    # Telegram
    telegram_bot_token: str = ""
    webhook_url: str = ""
    webhook_secret: str = ""

    # MongoDB
    mongo_uri: str = "mongodb://localhost:27017"
    database_name: str = "aircraft_bot"

    # Aircraft data providers
    adsb_lol_base_url: str = "https://api.adsb.lol/v2"
    adsb_fi_base_url: str = "https://opendata.adsb.fi/api/v2"
    opensky_base_url: str = "https://opensky-network.org/api"
    airplanes_live_base_url: str = "https://api.airplanes.live/v2"
    adsb_one_base_url: str = "https://api.adsb.one/v2"
    opensky_token_url: str = (
        "https://auth.opensky-network.org/auth/realms/"
        "opensky-network/protocol/openid-connect/token"
    )
    # Preferred hosted format: one OpenSky credential pair per secret slot.
    # Each OPENSKY_N may be JSON {"clientId":"...","clientSecret":"..."}
    # or the compact form clientId:clientSecret. The legacy aggregate JSON
    # variable remains supported for backwards compatibility.
    opensky_1: str = ""
    opensky_2: str = ""
    opensky_3: str = ""
    opensky_4: str = ""
    opensky_5: str = ""
    opensky_credentials_json: str = ""
    api_keys_dir: str = "api"

    # Flight-number route intelligence. The bulk route endpoint is preferred;
    # the single-route endpoint is an official fallback that also calculates
    # whether the route is plausible for the aircraft's live position.
    route_lookup_url: str = "https://api.adsb.lol/api/0/routeset"
    route_lookup_single_url: str = "https://api.adsb.lol/api/0/route"
    route_lookup_cache_seconds: int = 1200
    route_history_days: int = 3
    route_sample_interval_seconds: int = 30

    # Google Contrails API v2. Forecast fetches are background-only and cached
    # regionally by time/flight level. Missing credentials never disable the
    # deterministic Open-Meteo upper-air fallback.
    google_contrails_api_key: str = ""
    google_contrails_grid_url: str = "https://contrails.googleapis.com/v2/grids"

    # AI providers
    gemini_api_key: str = ""
    gemini_api_key_2: str = ""
    gemini_model_primary: str = "gemini-3.5-flash-lite"
    gemini_model_secondary: str = "gemini-3.5-flash"
    # User-facing preferred names are GROQ_KEY / GROQ_KEY_2. GROQ_API_KEY is
    # retained so existing deployments do not break.
    groq_key: str = ""
    groq_key_2: str = ""
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"

    # v3.2 Gemini photography intelligence
    gemini_photo_model: str = "gemini-3.8-flash"
    gemini_photo_fallback_model: str = "gemini-3.5-flash-lite"
    gemini_photo_timeout_seconds: float = 30.0
    open_meteo_forecast_url: str = "https://api.open-meteo.com/v1/forecast"
    open_meteo_air_quality_url: str = "https://air-quality-api.open-meteo.com/v1/air-quality"
    photography_http_timeout_seconds: float = 12.0
    photography_conditions_cache_seconds: int = 120

    # Trajectory prediction
    predictor_service_url: str = ""
    early_warning_buffer_km: float = 15.0

    # Monitoring
    poll_interval_seconds: int = 5
    default_radius_km: float = 15.0
    cooldown_minutes: int = 30

    # Learning
    learning_plane_threshold: int = 100
    relearn_plane_count: int = 25

    # Admin
    admin_telegram_id: int | None = None
    admin_password: str = ""

    # Private Antigravity prediction-lab worker
    agy_worker_url: str = ""
    agy_worker_token: str = ""

    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"


settings = Settings()
