"""Orchestration for v3.2 photography intelligence."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from app.aircraft.categories import AIRCRAFT_CATEGORIES, resolve_match_prefixes
from app.config import settings
from app.database import get_db, locations_col, notification_history_col, preferences_col
from app.photography.conditions import get_current_conditions
from app.photography.gemini import photography_ai
from app.photography.models import (
    AircraftPhotoContext,
    CameraProfile,
    LensProfile,
    PhotoRecommendation,
    PhotographyContext,
    WeatherContext,
)
from app.photography.solar import get_solar_context
from app.worker.geo import haversine, km_to_nautical_miles


class PhotographySetupError(RuntimeError):
    pass


class MissingLocationError(PhotographySetupError):
    pass


class MissingCameraError(PhotographySetupError):
    pass


def _profiles_col():
    return get_db()["camera_profiles"]


async def get_camera_setup(user_id: int) -> tuple[CameraProfile | None, LensProfile | None]:
    doc = await _profiles_col().find_one({"user_id": user_id})
    if not doc:
        return None, None
    camera = None
    lens = None
    try:
        if doc.get("camera"):
            camera = CameraProfile.model_validate(doc["camera"])
    except Exception:
        camera = None
    try:
        if doc.get("lens"):
            lens = LensProfile.model_validate(doc["lens"])
    except Exception:
        lens = None
    return camera, lens


async def identify_and_save_camera(user_id: int, user_text: str) -> CameraProfile:
    profile = await photography_ai.resolve_camera(user_text)
    await _profiles_col().update_one(
        {"user_id": user_id},
        {
            "$set": {
                "user_id": user_id,
                "camera": profile.model_dump(mode="json"),
                "camera_updated_at": datetime.now(timezone.utc),
            },
            "$setOnInsert": {"created_at": datetime.now(timezone.utc)},
        },
        upsert=True,
    )
    return profile


async def identify_and_save_lens(user_id: int, user_text: str) -> LensProfile:
    camera, _ = await get_camera_setup(user_id)
    profile = await photography_ai.resolve_lens(user_text, camera)
    await _profiles_col().update_one(
        {"user_id": user_id},
        {
            "$set": {
                "user_id": user_id,
                "lens": profile.model_dump(mode="json"),
                "lens_updated_at": datetime.now(timezone.utc),
            },
            "$setOnInsert": {"created_at": datetime.now(timezone.utc)},
        },
        upsert=True,
    )
    return profile


def _watched_prefixes(prefs: dict[str, Any] | None) -> set[str]:
    if not prefs:
        return set()
    selected = prefs.get("selected_categories", [])
    disabled = set(prefs.get("disabled_types", []))
    custom = prefs.get("custom_aircraft", [])
    base: set[str] = set(custom)
    for category in selected:
        base.update(t for t in AIRCRAFT_CATEGORIES.get(category, []) if t not in disabled)
    return resolve_match_prefixes(base)


async def _find_live_aircraft(
    latitude: float,
    longitude: float,
    radius_km: float,
    *,
    target_icao24: str = "",
    prefs: dict[str, Any] | None = None,
) -> AircraftPhotoContext | None:
    from app.worker.monitor import get_provider_manager

    radius_nm = min(250, max(60, int(km_to_nautical_miles(radius_km + 60))))
    manager = get_provider_manager()
    aircraft, _ = await manager.query_providers(
        latitude=latitude,
        longitude=longitude,
        radius_nm=radius_nm,
        provider_names=None,
    )
    if not aircraft:
        return None

    target = target_icao24.lower().strip()
    prefixes = _watched_prefixes(prefs)
    candidates: list[tuple[float, Any]] = []
    for ac in aircraft:
        if not ac.has_position:
            continue
        if target and (ac.icao24 or "").lower() != target:
            continue
        if not target and prefixes:
            ac_type = (ac.aircraft_type or "").upper().strip()
            if not ac_type or not any(ac_type.startswith(prefix) for prefix in prefixes):
                continue
        distance = haversine(latitude, longitude, ac.latitude, ac.longitude)
        if target or distance <= radius_km + 30:
            candidates.append((distance, ac))

    if not candidates:
        return None
    distance, ac = min(candidates, key=lambda pair: pair[0])
    return AircraftPhotoContext(
        icao24=ac.icao24 or "",
        aircraft_type=ac.aircraft_type or ac.display_type or "",
        callsign=ac.callsign or "",
        distance_km=round(distance, 2),
        altitude_m=ac.altitude,
        speed_ms=ac.velocity,
        heading_deg=ac.heading,
        latitude=ac.latitude,
        longitude=ac.longitude,
        live=True,
    )


def _aircraft_from_doc(doc: dict[str, Any]) -> AircraftPhotoContext:
    return AircraftPhotoContext(
        icao24=str(doc.get("aircraft_icao24") or ""),
        aircraft_type=str(doc.get("aircraft_type") or ""),
        callsign=str(doc.get("callsign") or ""),
        distance_km=float(doc["distance_km"]) if doc.get("distance_km") is not None else None,
        altitude_m=float(doc["altitude_m"]) if doc.get("altitude_m") is not None else None,
        speed_ms=float(doc["speed_ms"]) if doc.get("speed_ms") is not None else None,
        heading_deg=float(doc["heading_deg"]) if doc.get("heading_deg") is not None else None,
        latitude=float(doc["latitude"]) if doc.get("latitude") is not None else None,
        longitude=float(doc["longitude"]) if doc.get("longitude") is not None else None,
        eta_seconds=float(doc["eta_seconds"]) if doc.get("eta_seconds") is not None else None,
        live=False,
    )


async def _notification_fallback(user_id: int, notification_id: str) -> AircraftPhotoContext | None:
    if not notification_id:
        return None

    # This snapshot is written before Telegram sends the alert, so an immediate
    # tap always has plane context even before monitor history is finalized.
    snapshot = await get_db()["photo_alert_snapshots"].find_one(
        {"_id": notification_id, "user_id": user_id}
    )
    if snapshot:
        return _aircraft_from_doc(snapshot)

    doc = await notification_history_col().find_one({"_id": notification_id, "user_id": user_id})
    if not doc:
        return None
    return _aircraft_from_doc(doc)


async def build_photography_context(
    user_id: int,
    *,
    notification_id: str = "",
) -> PhotographyContext:
    location = await locations_col().find_one({"user_id": user_id})
    if not location:
        raise MissingLocationError("Set your monitoring location first with /location.")

    camera, lens = await get_camera_setup(user_id)
    if camera is None:
        raise MissingCameraError("Camera profile is not configured.")

    latitude = float(location["latitude"])
    longitude = float(location["longitude"])
    radius_km = float(location.get("radius_km", settings.default_radius_km))
    prefs = await preferences_col().find_one({"user_id": user_id})

    fallback = await _notification_fallback(user_id, notification_id) if notification_id else None
    target_icao = fallback.icao24 if fallback else ""

    # Start live refresh and atmospheric work together. The saved alert snapshot
    # is authoritative enough to begin an urgent recommendation, so live ADS-B
    # refresh is given only a short grace period after weather becomes ready.
    weather_task = asyncio.create_task(get_current_conditions(latitude, longitude))
    live_task = asyncio.create_task(
        _find_live_aircraft(
            latitude,
            longitude,
            radius_km,
            target_icao24=target_icao,
            prefs=prefs,
        )
    )

    weather = await weather_task
    live: AircraftPhotoContext | None = None
    if live_task.done():
        try:
            live = live_task.result()
        except Exception:
            live = None
    else:
        try:
            live = await asyncio.wait_for(asyncio.shield(live_task), timeout=1.5)
        except (asyncio.TimeoutError, Exception):
            live = None
            live_task.cancel()

    if live and fallback:
        # Preserve predictive ETA from the alert while refreshing the aircraft's
        # current position/distance/speed from ADS-B.
        if live.eta_seconds is None:
            live.eta_seconds = fallback.eta_seconds
        if not live.callsign:
            live.callsign = fallback.callsign
        if not live.aircraft_type:
            live.aircraft_type = fallback.aircraft_type

    aircraft = live or fallback
    solar = get_solar_context(
        latitude,
        longitude,
        weather.timezone,
        subject_latitude=aircraft.latitude if aircraft else None,
        subject_longitude=aircraft.longitude if aircraft else None,
    )
    return PhotographyContext(
        latitude=latitude,
        longitude=longitude,
        camera=camera,
        lens=lens,
        weather=weather,
        solar=solar,
        aircraft=aircraft,
    )


async def recommend_for_user(user_id: int, *, notification_id: str = "") -> tuple[PhotoRecommendation, PhotographyContext]:
    context = await build_photography_context(user_id, notification_id=notification_id)
    recommendation = await photography_ai.recommend(context)
    return recommendation, context


async def get_conditions_for_user(user_id: int) -> tuple[WeatherContext, Any]:
    location = await locations_col().find_one({"user_id": user_id})
    if not location:
        raise MissingLocationError("Set your monitoring location first with /location.")
    latitude = float(location["latitude"])
    longitude = float(location["longitude"])
    weather = await get_current_conditions(latitude, longitude)
    solar = get_solar_context(latitude, longitude, weather.timezone)
    return weather, solar
