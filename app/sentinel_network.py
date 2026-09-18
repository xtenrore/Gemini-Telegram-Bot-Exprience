"""Plane Alerts v4.0 Europe Prediction Lab sentinel network.

The sentinel network is shadow telemetry only. It rotates one free public ADS-B
query at a time across a small set of high-traffic European regions and stores
bounded callsign-keyed route traces for replay/audit. It never creates or
cancels user alerts and never calls AI or a paid API.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from app.database import get_db, system_status_col
from app.intelligence.route_history import normalize_flight_key
from app.worker import monitor
from app.worker.geo import haversine

logger = logging.getLogger(__name__)

POLL_INTERVAL_S = max(20.0, float(os.getenv("SENTINEL_POLL_INTERVAL_SECONDS", "30") or 30.0))
RADIUS_NM = max(55, min(140, int(os.getenv("SENTINEL_RADIUS_NM", "90") or 90)))
PUBLIC_PROVIDERS = ("adsb.lol", "adsb.fi", "airplanes.live", "adsb.one")
ROUTE_TTL_DAYS = 8
MAX_POINTS_PER_ROUTE_DAY = 480


@dataclass(frozen=True, slots=True)
class SentinelRegion:
    id: str
    name: str
    latitude: float
    longitude: float


# These are observation regions, not user locations and not notification sites.
# They deliberately span different traffic/ATC environments so Prediction Lab
# is not overfit to Istanbul or to one airport geometry.
EUROPE_SENTINELS: tuple[SentinelRegion, ...] = (
    SentinelRegion("istanbul", "Istanbul / Marmara", 41.10, 28.75),
    SentinelRegion("london", "London / South East", 51.35, -0.20),
    SentinelRegion("frankfurt", "Frankfurt / Rhine-Main", 50.05, 8.55),
    SentinelRegion("paris", "Paris / Île-de-France", 49.00, 2.35),
    SentinelRegion("amsterdam", "Amsterdam / Benelux", 52.30, 4.80),
    SentinelRegion("madrid", "Madrid / Central Spain", 40.45, -3.55),
    SentinelRegion("rome", "Rome / Central Italy", 41.85, 12.45),
    SentinelRegion("vienna", "Vienna / Central Europe", 48.15, 16.35),
)

_last_saved: dict[tuple[str, str], tuple[float, float, float]] = {}


def _sample_point(ac: Any, now: float) -> dict[str, Any]:
    return {
        "t": round(now, 1),
        "lat": round(float(ac.latitude), 5),
        "lon": round(float(ac.longitude), 5),
        "altitude_m": getattr(ac, "altitude", None),
        "heading_deg": getattr(ac, "heading", None),
        "speed_kts": getattr(ac, "ground_speed", None),
        "vertical_rate_mps": getattr(ac, "vertical_rate_mps", None),
        "icao24": str(getattr(ac, "icao24", "") or "").lower(),
    }


def _should_store(region_id: str, callsign: str, point: dict[str, Any]) -> bool:
    key = (region_id, callsign)
    now = float(point["t"])
    lat = float(point["lat"])
    lon = float(point["lon"])
    previous = _last_saved.get(key)
    if previous is not None:
        last_t, last_lat, last_lon = previous
        # Preserve turns while avoiding nearly-identical points from repeated
        # public-feed snapshots.
        if now - last_t < 45.0 and haversine(last_lat, last_lon, lat, lon) < 1.2:
            return False
    _last_saved[key] = (now, lat, lon)
    return True


async def _record_region(region: SentinelRegion, aircraft: list[Any], provider_name: str, now: float) -> int:
    db = get_db()
    day = datetime.fromtimestamp(now, timezone.utc).date().isoformat()
    expires_at = datetime.now(timezone.utc) + timedelta(days=ROUTE_TTL_DAYS)
    stored = 0

    for ac in aircraft:
        if not getattr(ac, "has_position", False):
            continue
        callsign = normalize_flight_key(getattr(ac, "callsign", ""))
        if not callsign:
            continue
        try:
            point = _sample_point(ac, now)
        except (TypeError, ValueError):
            continue
        if not _should_store(region.id, callsign, point):
            continue

        try:
            await db["prediction_sentinel_routes"].update_one(
                {"sentinel_id": region.id, "callsign": callsign, "utc_date": day},
                {
                    "$set": {
                        "sentinel_id": region.id,
                        "sentinel_name": region.name,
                        "callsign": callsign,
                        "utc_date": day,
                        "provider": provider_name,
                        "updated_at": datetime.now(timezone.utc),
                        "expires_at": expires_at,
                    },
                    "$push": {"points": {"$each": [point], "$slice": -MAX_POINTS_PER_ROUTE_DAY}},
                },
                upsert=True,
            )
            stored += 1
        except Exception:
            logger.exception("sentinel_route_store_failed region=%s callsign=%s", region.id, callsign)
    return stored


async def run_sentinel_network() -> None:
    """Continuously rotate one shadow-only public ADS-B query across Europe."""
    region_cursor = 0
    provider_cursor = 0
    # Let the production monitor complete its first provider cycle before this
    # independent shadow sampler begins.
    await asyncio.sleep(12.0)

    while True:
        started = time.monotonic()
        region = EUROPE_SENTINELS[region_cursor % len(EUROPE_SENTINELS)]
        provider = PUBLIC_PROVIDERS[provider_cursor % len(PUBLIC_PROVIDERS)]
        region_cursor += 1
        provider_cursor += 1
        stored = 0
        aircraft_count = 0
        try:
            manager = monitor.get_provider_manager()
            aircraft, _ = await manager.query_providers(
                latitude=region.latitude,
                longitude=region.longitude,
                radius_nm=RADIUS_NM,
                provider_names=[provider],
            )
            aircraft_count = len(aircraft)
            if aircraft:
                stored = await _record_region(region, aircraft, provider, time.time())
            try:
                await system_status_col().update_one(
                    {"_id": "prediction_lab_sentinels"},
                    {"$set": {
                        "enabled": True,
                        "mode": "shadow-only",
                        "regions": len(EUROPE_SENTINELS),
                        "last_region": region.id,
                        "last_provider": provider,
                        "last_aircraft": aircraft_count,
                        "last_points_stored": stored,
                        "poll_interval_seconds": POLL_INTERVAL_S,
                        "updated_at": datetime.now(timezone.utc),
                    }},
                    upsert=True,
                )
            except Exception:
                logger.debug("sentinel_status_store_failed", exc_info=True)
            logger.info(
                "prediction_sentinel_poll region=%s provider=%s aircraft=%d stored=%d",
                region.id,
                provider,
                aircraft_count,
                stored,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            # Sentinel failure must never affect the production alert monitor.
            logger.exception("prediction_sentinel_poll_failed region=%s provider=%s", region.id, provider)

        elapsed = time.monotonic() - started
        await asyncio.sleep(max(5.0, POLL_INTERVAL_S - elapsed))
