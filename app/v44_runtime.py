"""Runtime adapters for Plane Alerts v4.4.

Installed before monitor.py binds its imported helpers. The adapters preserve
all deterministic fallbacks and never add a network wait to the five-second
monitoring path.
"""
from __future__ import annotations

import contextvars
import logging
from dataclasses import dataclass
from typing import Any

from app.database import get_db
from app.decision_recorder import record_decision
from app.intelligence import environment as env_mod
from app.intelligence import upper_air as upper_mod
from app.intelligence.google_contrails import GoogleContrailResult, google_contrails
from app.worker import notifications as notification_mod

logger = logging.getLogger(__name__)
_INSTALLED = False
_google_context: contextvars.ContextVar[GoogleContrailResult | None] = contextvars.ContextVar(
    "plane_alerts_google_contrail", default=None
)
_BASE_UPPER = upper_mod.get_upper_air_profile
_BASE_ESTIMATE = env_mod.estimate_contrail
_BASE_CONDITIONS_LINE = notification_mod._conditions_line
_BASE_SEND_APPROACH = notification_mod.send_or_update_approach


@dataclass(slots=True)
class ContrailEstimateV44(env_mod.ContrailEstimate):
    source: str = "Deterministic upper-air fallback"
    probability: float | None = None
    google_forecast_time: str = ""
    google_flight_level: str = ""
    fallback_formation: str = ""
    fallback_persistence: str = ""


async def _upper_with_google(latitude: float, longitude: float, altitude_m: float | None = None):
    # Cache lookup is synchronous and network-free. Misses only schedule a
    # bounded background request; Open-Meteo remains the normal deterministic
    # fallback path for the current cycle.
    google = google_contrails.get_or_schedule(latitude, longitude, altitude_m)
    _google_context.set(google)
    return await _BASE_UPPER(latitude, longitude, altitude_m)


def _estimate_with_google(atm: env_mod.FlightLevelAtmosphere, aircraft_type: str = "") -> env_mod.ContrailEstimate:
    fallback = _BASE_ESTIMATE(atm, aircraft_type)
    google = _google_context.get()
    _google_context.set(None)
    if google is None or not google.available or google.probability is None:
        return ContrailEstimateV44(
            fallback.formation,
            fallback.persistence,
            fallback.confidence,
            fallback.formation_score,
            fallback.persistence_score,
            list(fallback.reasons),
            source="Deterministic upper-air fallback",
            probability=None,
        )

    probability = max(0.0, min(1.0, float(google.probability)))
    score = int(round(probability * 100.0))
    result = ContrailEstimateV44(
        google.formation,
        google.persistence,
        google.confidence,
        score,
        score,
        [
            f"Google Contrails v2 persistent formation forecast: ~{score}%",
            "deterministic upper-air model retained as fallback/comparison",
        ],
        source=google.source,
        probability=probability,
        google_forecast_time=google.forecast_time,
        google_flight_level=google.flight_level,
        fallback_formation=fallback.formation,
        fallback_persistence=fallback.persistence,
    )
    disagreement = google.formation != fallback.formation or google.persistence != fallback.persistence
    record_decision(
        subsystem="contrail",
        event="forecast_comparison",
        state_key=f"contrail:{google.flight_level}:{google.forecast_time}",
        state="disagreement" if disagreement else "agreement",
        decision="google_primary",
        reason_code="google_vs_deterministic_disagreement" if disagreement else "google_forecast_primary",
        dedup_signature={
            "probability_bucket": round(probability, 1),
            "google": [google.formation, google.persistence],
            "fallback": [fallback.formation, fallback.persistence],
        },
        evidence={
            "google_probability": probability,
            "google_formation": google.formation,
            "google_persistence": google.persistence,
            "google_confidence": google.confidence,
            "forecast_time": google.forecast_time,
            "flight_level": google.flight_level,
            "deterministic_formation": fallback.formation,
            "deterministic_persistence": fallback.persistence,
            "deterministic_confidence": fallback.confidence,
            "deterministic_formation_score": fallback.formation_score,
            "deterministic_persistence_score": fallback.persistence_score,
        },
    )
    return result


def _conditions_line_v44(environment) -> str | None:
    line = _BASE_CONDITIONS_LINE(environment)
    if not environment:
        return line
    contrail = environment.get("contrail")
    if not contrail:
        return line
    source = str(getattr(contrail, "source", "") or "")
    probability = getattr(contrail, "probability", None)
    if source.startswith("Google Contrails") and probability is not None:
        # Keep the UI compact and do not imply false precision.
        pct = int(round(float(probability) * 100.0))
        formation = str(getattr(contrail, "formation", "Unknown"))
        persistence = str(getattr(contrail, "persistence", "Unknown"))
        return f"Contrail {formation} · persistence {persistence} · ~{pct}% · Google forecast"
    if source and line:
        return line + " · fallback"
    return line


def _small_environment(environment: Any) -> dict[str, Any]:
    if not isinstance(environment, dict):
        return {}
    weather = environment.get("weather")
    atmosphere = environment.get("atmosphere")
    contrail = environment.get("contrail")
    solar = environment.get("solar")
    return {
        "weather": {
            "temperature_c": getattr(weather, "temperature_c", None),
            "visibility_m": getattr(weather, "visibility_m", None),
            "humidity_pct": getattr(weather, "relative_humidity_pct", None),
            "wind_kmh": getattr(weather, "wind_speed_kmh", None),
        } if weather else None,
        "atmosphere": {
            "heat_haze": getattr(atmosphere, "heat_haze", None),
            "clarity_score": getattr(atmosphere, "clarity_score", None),
            "clarity_label": getattr(atmosphere, "clarity_label", None),
        } if atmosphere else None,
        "solar": {
            "elevation_deg": getattr(solar, "elevation_deg", None),
            "lighting_relationship": getattr(solar, "lighting_relationship", None),
        } if solar else None,
        "contrail": {
            "formation": getattr(contrail, "formation", None),
            "persistence": getattr(contrail, "persistence", None),
            "confidence": getattr(contrail, "confidence", None),
            "source": getattr(contrail, "source", "Deterministic upper-air fallback"),
            "probability": getattr(contrail, "probability", None),
            "fallback_formation": getattr(contrail, "fallback_formation", None),
            "fallback_persistence": getattr(contrail, "fallback_persistence", None),
        } if contrail else None,
    }


async def _send_approach_v44(
    user_id: int,
    aircraft,
    prediction,
    stage: str,
    notification_id: str,
    message_id: int | None = None,
    *,
    camera=None,
    environment=None,
    previous_cpa_km=None,
    observed_closest_km=None,
    prediction_changed=False,
) -> int | None:
    delivered_message_id = await _BASE_SEND_APPROACH(
        user_id,
        aircraft,
        prediction,
        stage,
        notification_id,
        message_id,
        camera=camera,
        environment=environment,
        previous_cpa_km=previous_cpa_km,
        observed_closest_km=observed_closest_km,
        prediction_changed=prediction_changed,
    )
    delivered = delivered_message_id is not None
    decision_id = record_decision(
        subsystem="telegram_alert",
        event="delivery",
        state_key=f"delivery:{int(user_id)}:{notification_id}",
        state=stage,
        decision="delivered" if delivered else "delivery_failed",
        reason_code=f"telegram_{stage}",
        user_id=int(user_id),
        notification_id=notification_id,
        force=stage in {"cancelled", "passed"},
        dedup_signature={
            "stage": stage,
            "delivered": delivered,
            "cpa_bucket": round(float(getattr(prediction, "projected_closest_km", 0.0) or 0.0), 1),
        },
        evidence={
            "flight_number": getattr(aircraft, "callsign", ""),
            "icao24": getattr(aircraft, "icao24", ""),
            "aircraft_type": getattr(aircraft, "aircraft_type", ""),
            "position": {"latitude": getattr(aircraft, "latitude", None), "longitude": getattr(aircraft, "longitude", None)},
            "altitude_m": getattr(aircraft, "altitude", None),
            "groundspeed_kts": getattr(aircraft, "ground_speed", None),
            "track_deg": getattr(aircraft, "heading", None),
            "vertical_rate_mps": getattr(aircraft, "vertical_rate_mps", None),
            "user_distance_km": getattr(prediction, "current_distance_km", None),
            "straight_cpa_km": getattr(prediction, "projected_closest_km", None),
            "time_to_cpa_s": getattr(prediction, "time_to_cpa_s", None),
            "prediction_confidence": getattr(prediction, "confidence", None),
            "trajectory_state": getattr(prediction, "state", None),
            "eta_s": getattr(prediction, "time_to_cpa_s", None),
            "predicted_shooting_window": {
                "start_s": getattr(camera, "best_window_start_s", None),
                "end_s": getattr(camera, "best_window_end_s", None),
            } if camera else None,
            "camera_recommendation": {
                "shutter": getattr(camera, "shutter_speed", None),
                "aperture": getattr(camera, "aperture", None),
                "iso": getattr(camera, "iso", None),
                "focal_length": getattr(camera, "focal_length", None),
                "focal_range_mm": getattr(camera, "focal_range_mm", None),
            } if camera else None,
            "environment": _small_environment(environment),
            "alert_delivered": delivered,
            "telegram_message_id": delivered_message_id,
            "observed_closest_km": observed_closest_km,
            "previous_projected_cpa_km": previous_cpa_km,
        },
    )
    if decision_id and notification_id:
        try:
            await get_db()["notification_history"].update_one(
                {"_id": notification_id, "user_id": int(user_id)},
                {"$set": {"decision_record_id": decision_id, "alert_delivered": delivered}},
            )
        except Exception:
            logger.debug("decision_notification_link_failed notification=%s", notification_id, exc_info=True)
    return delivered_message_id


def install_v44_runtime_adapters() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    upper_mod.get_upper_air_profile = _upper_with_google
    env_mod.estimate_contrail = _estimate_with_google
    notification_mod._conditions_line = _conditions_line_v44
    notification_mod.send_or_update_approach = _send_approach_v44
    _INSTALLED = True
    logger.info("Plane Alerts v4.4 runtime adapters enabled: cached Google Contrails + Decision Recorder delivery linkage")
