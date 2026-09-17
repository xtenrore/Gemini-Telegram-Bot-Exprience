"""Reliability guards for the live Plane? monitoring loop.

These guards keep the deterministic alert path responsive when public ADS-B feeds
briefly return an empty response and ensure a directly observed aircraft inside the
configured radius cannot be missed merely because its CPA happened between polls.
Network route enrichment is moved off the critical monitoring path; locally stored
flight-number history remains an immediate veto source.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import replace
from typing import Any

from app.aircraft.providers import ProviderManager
from app.config import settings
from app.intelligence import route_history as route_mod
from app.intelligence import trajectory as trajectory_mod

logger = logging.getLogger(__name__)

_CONTINUITY_TTL_S = 28.0
_OPEN_SKY_FALLBACK_TIMEOUT_S = 2.5
_INSTALLED = False

_ORIGINAL_QUERY_PROVIDERS = ProviderManager.query_providers
_ORIGINAL_PREDICT_TRAJECTORY = trajectory_mod.predict_trajectory


def _within_query_area(ac: Any, latitude: float, longitude: float, radius_nm: int) -> bool:
    if getattr(ac, "latitude", None) is None or getattr(ac, "longitude", None) is None:
        return False
    try:
        distance_km = trajectory_mod.haversine_km(
            float(ac.latitude), float(ac.longitude), float(latitude), float(longitude)
        )
    except (TypeError, ValueError):
        return False
    return distance_km <= float(radius_nm) * 1.852 + 8.0


def _cache_live_aircraft(manager: ProviderManager, aircraft: list[Any], now: float) -> dict[str, tuple[float, Any]]:
    cache: dict[str, tuple[float, Any]] = getattr(manager, "_reliability_aircraft_cache", {})
    for ac in aircraft:
        key = str(getattr(ac, "icao24", "") or "").lower().strip()
        if key and getattr(ac, "has_position", False):
            cache[key] = (now, ac)
    cutoff = now - _CONTINUITY_TTL_S
    for key, (seen_at, _) in list(cache.items()):
        if seen_at < cutoff:
            cache.pop(key, None)
    manager._reliability_aircraft_cache = cache
    return cache


def _continuity_aircraft(
    manager: ProviderManager,
    live_aircraft: list[Any],
    *,
    latitude: float,
    longitude: float,
    radius_nm: int,
    now: float,
) -> list[Any]:
    cache = _cache_live_aircraft(manager, live_aircraft, now)
    present = {
        str(getattr(ac, "icao24", "") or "").lower().strip()
        for ac in live_aircraft
        if getattr(ac, "icao24", None)
    }
    recovered: list[Any] = []
    for key, (seen_at, ac) in cache.items():
        if key in present:
            continue
        elapsed = max(0.0, now - seen_at)
        if elapsed > _CONTINUITY_TTL_S or not _within_query_area(ac, latitude, longitude, radius_nm):
            continue
        try:
            previous_age = float(getattr(ac, "position_age_s", 0.0) or 0.0)
            recovered.append(
                ac.model_copy(
                    update={
                        "position_age_s": previous_age + elapsed,
                        "data_quality": "continuity-cache",
                    }
                )
            )
        except Exception:
            logger.debug("Unable to reuse cached ADS-B aircraft %s", key, exc_info=True)
    return recovered


async def _reliable_query_providers(
    self: ProviderManager,
    latitude: float,
    longitude: float,
    radius_nm: int = 250,
    provider_names: list[str] | None = None,
):
    aircraft, by_provider = await _ORIGINAL_QUERY_PROVIDERS(
        self,
        latitude=latitude,
        longitude=longitude,
        radius_nm=radius_nm,
        provider_names=provider_names,
    )
    now = time.time()

    # Only hedge the normal monitor query. Explicit provider diagnostics retain
    # their original semantics.
    if provider_names is None and not aircraft and getattr(self, "opensky", None) is not None:
        try:
            if self.opensky.can_request_now():
                backup_aircraft, backup_by_provider = await asyncio.wait_for(
                    _ORIGINAL_QUERY_PROVIDERS(
                        self,
                        latitude=latitude,
                        longitude=longitude,
                        radius_nm=radius_nm,
                        provider_names=["opensky"],
                    ),
                    timeout=_OPEN_SKY_FALLBACK_TIMEOUT_S,
                )
                if backup_aircraft:
                    aircraft = backup_aircraft
                    by_provider = {**by_provider, **backup_by_provider}
                    logger.warning(
                        "adsb_primary_blank_using_opensky recovered=%d",
                        len(backup_aircraft),
                    )
        except asyncio.TimeoutError:
            logger.warning("adsb_opensky_fallback_timeout")
        except Exception:
            logger.exception("adsb_opensky_fallback_failed")

    if provider_names is None:
        recovered = _continuity_aircraft(
            self,
            list(aircraft),
            latitude=latitude,
            longitude=longitude,
            radius_nm=radius_nm,
            now=now,
        )
        if recovered:
            aircraft = [*aircraft, *recovered]
            logger.warning(
                "adsb_continuity_recovery live=%d cached=%d total=%d",
                len(aircraft) - len(recovered),
                len(recovered),
                len(aircraft),
            )

    return aircraft, by_provider


def _recover_direct_presence(prediction: Any, alert_radius_km: float):
    """Treat a fresh, observed in-radius aircraft as an alert-worthy pass.

    A feed gap can make the next usable point arrive just after geometric CPA.
    The old predictor then marks it Passed before a first notification is ever
    created. If the latest ADS-B position is still physically inside the user's
    radius, presence is stronger evidence than projected CPA timing.
    """
    if bool(getattr(prediction, "stale", False)):
        return prediction
    try:
        current = float(getattr(prediction, "current_distance_km"))
        radius = float(alert_radius_km)
    except (TypeError, ValueError):
        return prediction
    if current > radius:
        return prediction

    score = max(0.36, float(getattr(prediction, "confidence_score", 0.0) or 0.0))
    confidence = str(getattr(prediction, "confidence", "Uncertain"))
    if score >= 0.78:
        confidence = "High"
    elif score >= 0.58:
        confidence = "Medium"
    elif score >= 0.36 and confidence == "Uncertain":
        confidence = "Low"

    return replace(
        prediction,
        state="Passing nearby",
        confidence=confidence,
        confidence_score=score,
        radius_entry_s=0.0,
        enters_alert_radius=True,
        already_passed=False,
        reason="fresh ADS-B position is directly inside the configured alert radius",
    )


def _reliable_predict_trajectory(samples, user_lat, user_lon, alert_radius_km, **kwargs):
    prediction = _ORIGINAL_PREDICT_TRAJECTORY(
        samples,
        user_lat,
        user_lon,
        alert_radius_km,
        **kwargs,
    )
    return _recover_direct_presence(prediction, alert_radius_km)


def _schedule_route_refresh(service: Any, key: str, ac: Any) -> None:
    tasks: dict[str, asyncio.Task] = getattr(service, "_reliability_route_tasks", {})
    existing = tasks.get(key)
    if existing is not None and not existing.done():
        return

    task = asyncio.create_task(service.resolve_route(ac), name=f"route-refresh:{key}")
    tasks[key] = task
    service._reliability_route_tasks = tasks

    def _finished(done: asyncio.Task, *, callsign: str = key) -> None:
        tasks.pop(callsign, None)
        try:
            done.result()
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("route_refresh_background_failed callsign=%s", callsign)

    task.add_done_callback(_finished)


async def _nonblocking_route_evaluate(
    self,
    ac: Any,
    pred: Any,
    *,
    user_lat: float,
    user_lon: float,
    alert_radius_km: float,
    current_samples,
):
    key = route_mod.normalize_flight_key(getattr(ac, "callsign", ""))
    if not key:
        return route_mod.RouteGateResult(False, "", "no usable flight-number callsign")

    try:
        history = await self._historical_paths(key)
    except Exception:
        logger.exception("flight_route_history_read_failed callsign=%s", key)
        history = []

    now = time.monotonic()
    cached = self._route_cache.get(key)
    route = cached[1] if cached else None
    cache_ttl = max(60, int(settings.route_lookup_cache_seconds)) if route is not None else 60
    cache_stale = cached is None or now - cached[0] >= cache_ttl
    if cache_stale:
        _schedule_route_refresh(self, key, ac)

    result = route_mod.evaluate_route_gate(
        callsign=key,
        current_path=current_samples,
        historical_paths=history,
        observer_lat=user_lat,
        observer_lon=user_lon,
        alert_radius_km=alert_radius_km,
        destination=route.destination if route else None,
        route_plausible=bool(route and route.plausible),
        aircraft_lat=getattr(ac, "latitude", None),
        aircraft_lon=getattr(ac, "longitude", None),
        altitude_m=getattr(ac, "altitude", None),
        vertical_rate_mps=getattr(ac, "vertical_rate_mps", None),
        speed_kts=getattr(ac, "ground_speed", None),
        time_to_cpa_s=getattr(pred, "time_to_cpa_s", None),
    )

    # Historical routing is useful before the pass. It must never veto a plane
    # that is freshly observed physically inside the user's configured radius.
    try:
        directly_inside = (
            not bool(getattr(pred, "stale", False))
            and float(getattr(pred, "current_distance_km")) <= float(alert_radius_km)
        )
    except (TypeError, ValueError):
        directly_inside = False
    if directly_inside and result.suppress_alert:
        result = replace(
            result,
            suppress_alert=False,
            reason="direct observed presence inside alert radius overrides historical route veto",
            expected_turn_pending=False,
        )

    logger.info(
        "route_gate callsign=%s suppress=%s history_days=%d similar_days=%d similarity_km=%s destination=%s expected_turn=%s reason=%s",
        result.callsign,
        result.suppress_alert,
        result.history_days,
        result.similar_days,
        f"{result.similarity_km:.2f}" if result.similarity_km is not None else "na",
        result.destination_code or "unknown",
        result.expected_turn_pending,
        result.reason,
    )
    return result


def install_reliability_guards() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    ProviderManager.query_providers = _reliable_query_providers
    trajectory_mod.predict_trajectory = _reliable_predict_trajectory
    route_mod.RouteHistoryService.evaluate = _nonblocking_route_evaluate
    _INSTALLED = True
    logger.info(
        "Aircraft reliability guards enabled: OpenSky blank-cycle fallback, %.0fs continuity cache, direct in-radius recovery, non-blocking route enrichment",
        _CONTINUITY_TTL_S,
    )
