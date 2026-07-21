"""Background aircraft monitoring loop.

Every polling interval:
1. Fetch all active user locations from MongoDB
2. Group users by geohash region
3. For each region, query ALL providers in parallel and merge results
4. Score provider coverage for each region (all nearby planes, not just selected)
5. Match aircraft against user preferences and send notifications
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from app.aircraft.categories import (
    get_all_types_for_categories,
    resolve_match_prefixes,
)
from app.aircraft.providers import ProviderManager
from app.config import settings
from app.database import (
    locations_col,
    notification_history_col,
    preferences_col,
    users_col,
)
from app.worker.geo import (
    bounding_box,
    haversine,
    km_to_nautical_miles,
    merge_bounding_boxes,
)
from app.worker.notifications import send_aircraft_notification

logger = logging.getLogger(__name__)

# Shared provider manager (reused across monitor cycles)
_provider_manager = ProviderManager()


def get_provider_manager() -> ProviderManager:
    """Return the shared ProviderManager singleton (for admin routes)."""
    return _provider_manager


async def init_provider_manager() -> None:
    """Initialise the provider manager (load cached coverage data)."""
    await _provider_manager.load_coverage_from_db()


async def run_monitor_cycle() -> None:
    """Execute a single monitoring cycle.

    Called by the scheduler every ``settings.poll_interval_seconds``.
    """
    try:
        await _monitor_cycle()
    except Exception:
        logger.exception("Monitor cycle failed unexpectedly")


# ── Cycle stats (for admin dashboard) ────────────────────────────────────────
_last_cycle_time: float = 0.0
_last_cycle_duration: float = 0.0
_total_cycles: int = 0


def get_cycle_stats() -> dict:
    """Return monitoring cycle stats for the admin dashboard."""
    return {
        "last_cycle_time": _last_cycle_time,
        "last_cycle_duration_ms": round(_last_cycle_duration * 1000, 1),
        "total_cycles": _total_cycles,
    }


async def _monitor_cycle() -> None:
    """Internal implementation of the monitoring cycle."""
    global _last_cycle_time, _last_cycle_duration, _total_cycles  # noqa: PLW0603
    import time

    cycle_start = time.time()

    # ── 1. Fetch active users with locations ─────────────────────────────
    active_users = await _get_active_users()
    if not active_users:
        logger.debug("No active users with locations -- skipping cycle.")
        _last_cycle_time = time.time()
        _last_cycle_duration = time.time() - cycle_start
        _total_cycles += 1
        return

    logger.info("Monitor cycle: %d active user(s)", len(active_users))

    # ── 2. Group users by geohash ────────────────────────────────────────
    regions: dict[str, list[dict]] = defaultdict(list)
    for u in active_users:
        gh = u["location"]["geohash"]
        regions[gh].append(u)

    logger.info("Users grouped into %d region(s)", len(regions))

    # ── 3. For each region, fetch aircraft and match ─────────────────────
    total_notifications = 0

    for geohash_key, region_users in regions.items():
        try:
            notif_count = await _process_region(geohash_key, region_users)
            total_notifications += notif_count
        except Exception:
            logger.exception("Error processing region %s", geohash_key)

    _last_cycle_time = time.time()
    _last_cycle_duration = time.time() - cycle_start
    _total_cycles += 1

    if total_notifications > 0:
        logger.info(
            "Cycle #%d complete -- %d notification(s) sent in %.1fs.",
            _total_cycles,
            total_notifications,
            _last_cycle_duration,
        )
    else:
        logger.debug(
            "Cycle #%d complete -- no matches (%.1fs).",
            _total_cycles,
            _last_cycle_duration,
        )


async def _get_active_users() -> list[dict]:
    """Fetch users who have completed setup and have a location set."""
    cursor = users_col().find({"setup_complete": True}, {"user_id": 1})
    user_ids = [doc["user_id"] async for doc in cursor]

    if not user_ids:
        return []

    results = []
    for uid in user_ids:
        loc = await locations_col().find_one({"user_id": uid})
        prefs = await preferences_col().find_one({"user_id": uid})
        if loc and prefs:
            results.append({
                "user_id": uid,
                "location": loc,
                "preferences": prefs,
            })

    return results


async def _process_region(geohash_key: str, region_users: list[dict]) -> int:
    """Fetch aircraft for a region from ALL providers and match users.

    The provider manager queries all providers in parallel, merges results,
    and updates coverage scoring for this geohash region.  Coverage scoring
    operates on ALL detected aircraft (not just user-selected types) so the
    system learns true provider coverage for the area.

    Returns the number of notifications sent.
    """
    # Build a bounding box that covers all users in this region
    boxes = []
    for u in region_users:
        loc = u["location"]
        radius = loc.get("radius_km", settings.default_radius_km)
        boxes.append(bounding_box(loc["latitude"], loc["longitude"], radius))

    merged_box = merge_bounding_boxes(boxes)
    center_lat = (merged_box[0] + merged_box[1]) / 2
    center_lon = (merged_box[2] + merged_box[3]) / 2

    # Compute radius in nautical miles for the API call
    diag_km = haversine(merged_box[0], merged_box[2], merged_box[1], merged_box[3])
    radius_nm = int(km_to_nautical_miles(diag_km / 2) + 10)  # +10 nm buffer
    radius_nm = min(radius_nm, 250)  # API max is typically 250 nm

    # Query ALL providers, merge, and score coverage
    # This fetches ALL nearby planes (for coverage scoring) — user filtering
    # happens only at the notification step.
    aircraft_list = await _provider_manager.query_all_providers(
        latitude=center_lat,
        longitude=center_lon,
        radius_nm=radius_nm,
        geohash=geohash_key,
    )

    if not aircraft_list:
        return 0

    logger.debug(
        "Region %s: %d merged aircraft, checking %d user(s)",
        geohash_key,
        len(aircraft_list),
        len(region_users),
    )

    # Match against each user's preferences
    notification_count = 0
    for u in region_users:
        count = await _match_user_aircraft(u, aircraft_list)
        notification_count += count

    return notification_count


async def _match_user_aircraft(user_data: dict, aircraft_list: list) -> int:
    """Check all aircraft against a single user's preferences.

    Only aircraft matching the user's selected categories/custom types
    trigger notifications.  The full aircraft list is used for coverage
    scoring but filtering happens here.

    Returns the number of notifications sent for this user.
    """
    user_id = user_data["user_id"]
    loc = user_data["location"]
    prefs = user_data["preferences"]
    radius_km = loc.get("radius_km", settings.default_radius_km)

    # Build the set of types this user cares about
    selected_cats = prefs.get("selected_categories", [])
    custom_types = prefs.get("custom_aircraft", [])
    base_types = get_all_types_for_categories(selected_cats)
    base_types.update(custom_types)
    
    # Expand to family prefixes (e.g. B738 -> B73)
    watched_prefixes = resolve_match_prefixes(base_types)

    if not watched_prefixes:
        return 0

    user_lat = loc["latitude"]
    user_lon = loc["longitude"]
    count = 0

    for ac in aircraft_list:
        # Must have a valid position
        if not ac.has_position:
            continue

        # Must match a watched prefix (this handles both exact and family matching)
        ac_type = ac.aircraft_type.upper()
        if not ac_type or not any(ac_type.startswith(prefix) for prefix in watched_prefixes):
            continue

        # Must be within radius
        distance = haversine(user_lat, user_lon, ac.latitude, ac.longitude)
        if distance > radius_km:
            continue

        # Check cooldown
        if await _is_in_cooldown(user_id, ac.icao24):
            continue

        # Send notification!
        success = await send_aircraft_notification(
            user_id=user_id,
            aircraft=ac,
            distance_km=distance,
        )

        if success:
            await _set_cooldown(user_id, ac.icao24)
            count += 1

    return count


async def _is_in_cooldown(user_id: int, icao24: str) -> bool:
    """Check if a notification for this aircraft was recently sent to this user."""
    now = datetime.now(timezone.utc)
    doc = await notification_history_col().find_one({
        "user_id": user_id,
        "aircraft_icao24": icao24,
        "cooldown_until": {"$gt": now},
    })
    return doc is not None


async def _set_cooldown(user_id: int, icao24: str) -> None:
    """Record a notification and set the cooldown window."""
    now = datetime.now(timezone.utc)
    cooldown_until = now + timedelta(minutes=settings.cooldown_minutes)

    await notification_history_col().insert_one({
        "user_id": user_id,
        "aircraft_icao24": icao24,
        "notified_at": now,
        "cooldown_until": cooldown_until,
    })
