"""Destination-aware route guard for Plane? predictive approach alerts.

This guard fixes a specific class of false positives near major airports: a simple
short-horizon CPA extrapolation can extend an arriving aircraft through the
observer even though the flight is about to turn toward its known destination.

The guard is deliberately conservative:
* live geometry still creates the candidate;
* flight-route data may only veto a projected alert, never create one;
* a fresh aircraft physically inside the user's radius always wins;
* route lookup has a short grace period and then fails open, so a provider
  outage cannot permanently silence alerts.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import replace
from typing import Any, Iterable
from urllib.parse import quote

from app.aircraft.providers import get_http_client
from app.config import settings
from app.intelligence import route_history as route_mod
from app.intelligence.trajectory import bearing_deg, haversine_km

logger = logging.getLogger(__name__)

_ADSB_IM_ROUTESET = "https://adsb.im/api/0/routeset"
_ADSBDB_CALLSIGN = "https://api.adsbdb.com/v0/callsign"
_SOURCE_TIMEOUT_S = 2.2
_NEGATIVE_CACHE_S = 20.0
_INITIAL_ROUTE_GRACE_S = 12.0
_TERMINAL_DESTINATION_KM = 160.0
_TERMINAL_ALTITUDE_M = 8000.0


def _angle_delta(a: float, b: float) -> float:
    return (a - b + 180.0) % 360.0 - 180.0


def _airport_from_adsbdb(raw: Any) -> route_mod.AirportInfo | None:
    if not isinstance(raw, dict):
        return None
    try:
        lat = float(raw["latitude"]) if raw.get("latitude") is not None else None
        lon = float(raw["longitude"]) if raw.get("longitude") is not None else None
    except (TypeError, ValueError):
        lat = lon = None
    return route_mod.AirportInfo(
        icao=str(raw.get("icao_code") or "").upper(),
        iata=str(raw.get("iata_code") or "").upper(),
        name=str(raw.get("name") or ""),
        latitude=lat,
        longitude=lon,
    )


def _route_is_position_plausible(
    origin: route_mod.AirportInfo | None,
    destination: route_mod.AirportInfo | None,
    latitude: float,
    longitude: float,
) -> bool:
    """Sanity-check static callsign data against the live aircraft position."""
    if (
        origin is None
        or destination is None
        or origin.latitude is None
        or origin.longitude is None
        or destination.latitude is None
        or destination.longitude is None
    ):
        return False

    to_origin = haversine_km(latitude, longitude, origin.latitude, origin.longitude)
    to_destination = haversine_km(
        latitude, longitude, destination.latitude, destination.longitude
    )
    if min(to_origin, to_destination) <= 320.0:
        return True

    direct = haversine_km(
        origin.latitude, origin.longitude, destination.latitude, destination.longitude
    )
    if direct < 25.0:
        return False
    route_via_aircraft = to_origin + to_destination
    excess = max(0.0, route_via_aircraft - direct)
    return excess <= max(180.0, direct * 0.18)


def _route_from_adsbdb(
    callsign: str,
    payload: Any,
    *,
    latitude: float,
    longitude: float,
) -> route_mod.FlightRouteInfo | None:
    if not isinstance(payload, dict):
        return None
    response = payload.get("response")
    if not isinstance(response, dict):
        return None
    raw = response.get("flightroute")
    if not isinstance(raw, dict):
        return None

    origin = _airport_from_adsbdb(raw.get("origin"))
    destination = _airport_from_adsbdb(raw.get("destination"))
    if origin is None or destination is None:
        return None
    plausible = _route_is_position_plausible(
        origin, destination, float(latitude), float(longitude)
    )
    codes = f"{origin.code}-{destination.code}"
    return route_mod.FlightRouteInfo(
        callsign=callsign,
        airport_codes=codes,
        plausible=plausible,
        origin=origin,
        destination=destination,
    )


async def _fetch_routeset(
    client: Any,
    url: str,
    callsign: str,
    latitude: float,
    longitude: float,
) -> route_mod.FlightRouteInfo | None:
    response = None
    try:
        response = await client.post(
            url,
            json={
                "planes": [
                    {"callsign": callsign, "lat": latitude, "lng": longitude}
                ]
            },
            timeout=_SOURCE_TIMEOUT_S,
        )
        response.raise_for_status()
        return route_mod._route_info(callsign, response.json())
    except Exception as exc:
        logger.info(
            "route_guard_routeset_failed callsign=%s host=%s status=%s error=%s",
            callsign,
            url,
            getattr(response, "status_code", "na"),
            type(exc).__name__,
        )
        return None


async def _fetch_adsbdb(
    client: Any,
    callsign: str,
    latitude: float,
    longitude: float,
) -> route_mod.FlightRouteInfo | None:
    response = None
    try:
        response = await client.get(
            f"{_ADSBDB_CALLSIGN}/{quote(callsign, safe='')}",
            timeout=_SOURCE_TIMEOUT_S,
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return _route_from_adsbdb(
            callsign,
            response.json(),
            latitude=latitude,
            longitude=longitude,
        )
    except Exception as exc:
        logger.info(
            "route_guard_adsbdb_failed callsign=%s status=%s error=%s",
            callsign,
            getattr(response, "status_code", "na"),
            type(exc).__name__,
        )
        return None


def _choose_route(
    callsign: str,
    candidates: list[tuple[str, route_mod.FlightRouteInfo | None]],
) -> tuple[route_mod.FlightRouteInfo | None, str]:
    usable = [(source, route) for source, route in candidates if route is not None]
    if not usable:
        return None, ""

    plausible = [(source, route) for source, route in usable if route.plausible]
    pool = plausible or usable

    destinations = {
        (route.destination.code if route.destination else "").upper()
        for _, route in plausible
        if route.destination and route.destination.code
    }
    if len(destinations) > 1:
        logger.warning(
            "route_guard_source_conflict callsign=%s destinations=%s",
            callsign,
            ",".join(sorted(destinations)),
        )
        return None, "conflict"

    # Source order is intentional: ADSB.im has live-position plausibility,
    # ADSBdb gives an independent callsign database fallback, and the configured
    # endpoint remains a compatibility fallback.
    return pool[0][1], pool[0][0]


async def resolve_route_resilient(
    self: route_mod.RouteHistoryService,
    ac: Any,
) -> route_mod.FlightRouteInfo | None:
    key = route_mod.normalize_flight_key(getattr(ac, "callsign", ""))
    if (
        not key
        or getattr(ac, "latitude", None) is None
        or getattr(ac, "longitude", None) is None
    ):
        return None

    now = time.monotonic()
    cached = self._route_cache.get(key)
    if cached:
        ttl = (
            max(60, int(settings.route_lookup_cache_seconds))
            if cached[1] is not None
            else _NEGATIVE_CACHE_S
        )
        if now - cached[0] < ttl:
            return cached[1]

    latitude = float(ac.latitude)
    longitude = float(ac.longitude)
    client = await get_http_client()

    configured = str(getattr(settings, "route_lookup_url", "") or "").strip()
    source_urls: list[tuple[str, str]] = [("adsb.im", _ADSB_IM_ROUTESET)]
    if configured and configured.rstrip("/") != _ADSB_IM_ROUTESET.rstrip("/"):
        source_urls.append(("configured", configured))

    tasks: list[asyncio.Task] = [
        asyncio.create_task(
            _fetch_routeset(client, url, key, latitude, longitude),
            name=f"route:{key}:{source}",
        )
        for source, url in source_urls
    ]
    tasks.append(
        asyncio.create_task(
            _fetch_adsbdb(client, key, latitude, longitude),
            name=f"route:{key}:adsbdb",
        )
    )

    results = await asyncio.gather(*tasks, return_exceptions=True)
    candidates: list[tuple[str, route_mod.FlightRouteInfo | None]] = []
    for (source, _), value in zip(source_urls, results[: len(source_urls)]):
        candidates.append(
            (source, value if isinstance(value, route_mod.FlightRouteInfo) else None)
        )
    adsbdb_value = results[-1]
    candidates.insert(
        1,
        (
            "adsbdb",
            adsbdb_value
            if isinstance(adsbdb_value, route_mod.FlightRouteInfo)
            else None,
        ),
    )

    route, source = _choose_route(key, candidates)
    self._route_cache[key] = (time.monotonic(), route)
    sources: dict[str, str] = getattr(self, "_route_guard_sources", {})
    sources[key] = source
    self._route_guard_sources = sources

    if route is not None:
        logger.info(
            "route_guard_resolved callsign=%s source=%s airports=%s plausible=%s destination=%s",
            key,
            source,
            route.airport_codes,
            route.plausible,
            route.destination.code if route.destination else "unknown",
        )
    else:
        logger.warning(
            "route_guard_unresolved callsign=%s sources=%s",
            key,
            ",".join(source for source, _ in candidates),
        )
    return route


def _point_values(point: Any) -> tuple[float, float, float | None] | None:
    try:
        if isinstance(point, dict):
            lat = float(point.get("latitude", point.get("lat")))
            lon = float(point.get("longitude", point.get("lon")))
            horizontal = point.get("horizontal_km")
        else:
            lat = float(point.latitude)
            lon = float(point.longitude)
            horizontal = getattr(point, "horizontal_km", None)
        return lat, lon, float(horizontal) if horizontal is not None else None
    except (TypeError, ValueError, AttributeError):
        return None


def _projected_cpa_destination_distance(
    projected_path: Iterable[Any] | None,
    *,
    observer_lat: float,
    observer_lon: float,
    destination: route_mod.AirportInfo,
) -> float | None:
    if (
        projected_path is None
        or destination.latitude is None
        or destination.longitude is None
    ):
        return None
    candidates: list[tuple[float, float, float]] = []
    for point in projected_path:
        values = _point_values(point)
        if values is None:
            continue
        lat, lon, horizontal = values
        if horizontal is None:
            horizontal = haversine_km(lat, lon, observer_lat, observer_lon)
        candidates.append((horizontal, lat, lon))
    if not candidates:
        return None
    _, lat, lon = min(candidates, key=lambda item: item[0])
    return haversine_km(
        lat,
        lon,
        float(destination.latitude),
        float(destination.longitude),
    )


def evaluate_route_gate_destination_aware(
    *,
    callsign: str,
    current_path: Iterable[Any],
    historical_paths: Iterable[Iterable[Any]],
    observer_lat: float,
    observer_lon: float,
    alert_radius_km: float,
    destination: route_mod.AirportInfo | None = None,
    route_plausible: bool = False,
    aircraft_lat: float | None = None,
    aircraft_lon: float | None = None,
    altitude_m: float | None = None,
    vertical_rate_mps: float | None = None,
    speed_kts: float | None = None,
    time_to_cpa_s: float | None = None,
    projected_path: Iterable[Any] | None = None,
    heading_deg: float | None = None,
) -> route_mod.RouteGateResult:
    base = route_mod.evaluate_route_gate(
        callsign=callsign,
        current_path=current_path,
        historical_paths=historical_paths,
        observer_lat=observer_lat,
        observer_lon=observer_lon,
        alert_radius_km=alert_radius_km,
        destination=destination,
        route_plausible=route_plausible,
        aircraft_lat=aircraft_lat,
        aircraft_lon=aircraft_lon,
        altitude_m=altitude_m,
        vertical_rate_mps=vertical_rate_mps,
        speed_kts=speed_kts,
        time_to_cpa_s=time_to_cpa_s,
    )
    if base.suppress_alert:
        return base
    if (
        not route_plausible
        or destination is None
        or destination.latitude is None
        or destination.longitude is None
        or aircraft_lat is None
        or aircraft_lon is None
        or time_to_cpa_s is None
        or time_to_cpa_s <= 20.0
    ):
        return base

    current_observer = haversine_km(
        float(aircraft_lat), float(aircraft_lon), observer_lat, observer_lon
    )
    # Once the aircraft is physically inside the requested radius, observed
    # presence is authoritative; route knowledge must not hide it.
    if current_observer <= alert_radius_km:
        return base

    destination_distance = haversine_km(
        float(aircraft_lat),
        float(aircraft_lon),
        float(destination.latitude),
        float(destination.longitude),
    )
    observer_destination = haversine_km(
        observer_lat,
        observer_lon,
        float(destination.latitude),
        float(destination.longitude),
    )
    terminal_phase = (
        destination_distance <= _TERMINAL_DESTINATION_KM
        and (
            (altitude_m is not None and float(altitude_m) <= _TERMINAL_ALTITUDE_M)
            or (
                vertical_rate_mps is not None
                and float(vertical_rate_mps) <= -0.25
            )
        )
    )
    if not terminal_phase:
        return base
    if observer_destination <= alert_radius_km + max(4.0, alert_radius_km * 0.25):
        return base

    cpa_destination = _projected_cpa_destination_distance(
        projected_path,
        observer_lat=observer_lat,
        observer_lon=observer_lon,
        destination=destination,
    )
    if cpa_destination is None:
        return base

    outward_growth = cpa_destination - destination_distance
    required_growth = max(3.5, min(10.0, destination_distance * 0.10))

    heading_conflict = True
    if heading_deg is not None:
        destination_bearing = bearing_deg(
            float(aircraft_lat),
            float(aircraft_lon),
            float(destination.latitude),
            float(destination.longitude),
        )
        heading_conflict = (
            abs(_angle_delta(float(heading_deg), destination_bearing)) >= 18.0
        )

    if outward_growth >= required_growth and heading_conflict:
        return replace(
            base,
            suppress_alert=True,
            reason=(
                f"known destination {destination.code} requires a terminal turn: "
                "straight-line observer CPA moves the aircraft away from its destination"
            ),
            destination_code=destination.code,
            destination_distance_km=destination_distance,
            expected_turn_pending=True,
            route_plausible=True,
        )
    return base


async def evaluate_route_resilient(
    self: route_mod.RouteHistoryService,
    ac: Any,
    pred: Any,
    *,
    user_lat: float,
    user_lon: float,
    alert_radius_km: float,
    current_samples: Iterable[Any],
) -> route_mod.RouteGateResult:
    key = route_mod.normalize_flight_key(getattr(ac, "callsign", ""))
    if not key:
        return route_mod.RouteGateResult(
            False, "", "no usable flight-number callsign"
        )

    try:
        history = await self._historical_paths(key)
    except Exception:
        logger.exception("flight_route_history_read_failed callsign=%s", key)
        history = []

    try:
        directly_inside = (
            not bool(getattr(pred, "stale", False))
            and float(getattr(pred, "current_distance_km"))
            <= float(alert_radius_km)
        )
    except (TypeError, ValueError):
        directly_inside = False

    now = time.monotonic()
    cached = self._route_cache.get(key)
    route = None
    if cached:
        ttl = (
            max(60, int(settings.route_lookup_cache_seconds))
            if cached[1] is not None
            else _NEGATIVE_CACHE_S
        )
        if now - cached[0] < ttl:
            route = cached[1]
        else:
            cached = None

    pending: dict[str, float] = getattr(self, "_route_guard_pending_since", {})
    if route is None and not directly_inside:
        pending.setdefault(key, now)
        self._route_guard_pending_since = pending

        # On the first predictive candidate, spend a small bounded amount of
        # time resolving the route. All sources are queried concurrently.
        if cached is None:
            try:
                route = await asyncio.wait_for(
                    asyncio.shield(resolve_route_resilient(self, ac)),
                    timeout=_SOURCE_TIMEOUT_S + 0.35,
                )
            except asyncio.TimeoutError:
                logger.info("route_guard_initial_lookup_timeout callsign=%s", key)
            except Exception:
                logger.exception("route_guard_initial_lookup_failed callsign=%s", key)

        if route is None and time.monotonic() - pending[key] < _INITIAL_ROUTE_GRACE_S:
            return route_mod.RouteGateResult(
                True,
                key,
                "route resolution pending before first projected approach alert",
                history_days=len(history),
            )
    else:
        pending.pop(key, None)

    if route is not None:
        pending.pop(key, None)

    result = evaluate_route_gate_destination_aware(
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
        projected_path=getattr(pred, "path", None),
        heading_deg=getattr(ac, "heading", None),
    )

    if directly_inside and result.suppress_alert:
        result = replace(
            result,
            suppress_alert=False,
            reason="direct observed presence inside alert radius overrides route veto",
            expected_turn_pending=False,
        )

    source = getattr(self, "_route_guard_sources", {}).get(key, "")
    logger.info(
        "route_gate callsign=%s suppress=%s history_days=%d similar_days=%d "
        "similarity_km=%s destination=%s expected_turn=%s source=%s reason=%s",
        result.callsign,
        result.suppress_alert,
        result.history_days,
        result.similar_days,
        f"{result.similarity_km:.2f}" if result.similarity_km is not None else "na",
        result.destination_code or "unknown",
        result.expected_turn_pending,
        source or "none",
        result.reason,
    )
    return result


def install_route_guard() -> None:
    """Install the destination-aware guard after the reliability monkey patches."""
    route_mod.RouteHistoryService.resolve_route = resolve_route_resilient
    route_mod.RouteHistoryService.evaluate = evaluate_route_resilient
    logger.info(
        "Destination-aware route guard enabled: adsb.im + ADSBdb fallback, "
        "%.0fs first-alert grace, terminal-turn veto",
        _INITIAL_ROUTE_GRACE_S,
    )
