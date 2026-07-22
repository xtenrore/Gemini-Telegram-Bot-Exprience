"""HTTP Client for sending trajectory prediction requests to VM 2 Predictor Microservice."""

from __future__ import annotations

import logging
import httpx

from app.config import settings

logger = logging.getLogger(__name__)


async def predict_aircraft_intercept(
    aircraft,
    user_id: int | str,
    user_lat: float,
    user_lon: float,
    radius_km: float,
    buffer_km: float = 15.0,
    lookahead_seconds: int = 180,
) -> dict | None:
    """Send aircraft telemetry and user location to VM 2 Predictor Microservice.

    Returns dict with ``should_notify``, ``eta_seconds``, ``closest_pass_km``,
    ``pass_type``, and ``reason``, or ``None`` if VM 2 is unconfigured or fails.
    """
    url = settings.predictor_service_url.strip()
    if not url:
        return None

    # Ensure URL ends with /predict
    if not url.endswith("/predict"):
        url = url.rstrip("/") + "/predict"

    # Extract flight parameters safely
    heading = getattr(aircraft, "track", None) or getattr(aircraft, "heading", 0.0) or 0.0
    speed_kts = getattr(aircraft, "ground_speed", None) or getattr(aircraft, "speed", 0.0) or 0.0
    turn_rate = getattr(aircraft, "turn_rate", 0.0) or 0.0
    altitude_ft = getattr(aircraft, "altitude", None)

    payload = {
        "aircraft": {
            "icao24": aircraft.icao24,
            "callsign": getattr(aircraft, "callsign", None),
            "lat": aircraft.latitude,
            "lon": aircraft.longitude,
            "altitude_ft": altitude_ft,
            "speed_kts": float(speed_kts),
            "heading": float(heading),
            "turn_rate_deg_s": float(turn_rate),
        },
        "target": {
            "user_id": str(user_id),
            "user_lat": user_lat,
            "user_lon": user_lon,
            "radius_km": radius_km,
        },
        "lookahead_seconds": lookahead_seconds,
        "buffer_km": buffer_km,
    }

    try:
        async with httpx.AsyncClient(timeout=1.5) as client:
            response = await client.post(url, json=payload)
            if response.status_code == 200:
                return response.json()
            else:
                logger.warning("VM 2 Predictor returned status %d: %s", response.status_code, response.text)
                return None
    except Exception as exc:
        logger.debug("VM 2 Predictor request failed (falling back to direct match): %s", exc)
        return None
