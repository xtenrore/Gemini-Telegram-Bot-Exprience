"""Background aircraft monitoring loop.

Every polling interval:
1. Fetch all active user locations from MongoDB
2. Group users by geohash region
3. For each region:
   - Determine whether users are learning or post-learning
   - Query appropriate providers in parallel (all 5 during learning, selected after)
   - Record observation data into ProviderLearner (background silent learning)
   - Match aircraft against user preferences and send notifications with feedback buttons
"""

from __future__ import annotations

import logging

import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from app.aircraft.ai_judge import ai_judge
from app.aircraft.categories import (
    get_all_types_for_categories,
    resolve_match_prefixes,
)
from app.aircraft.learner import provider_learner
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
    is_within_square_and_circle,
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


async def init_services() -> None:
    """Initialise background services (AI Judge)."""
    ai_judge.initialize()


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

    # Ensure AI judge is ready
    if not ai_judge._initialized:
        ai_judge.initialize()

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
    """Fetch aircraft for a region and match users.

    During learning, queries ALL providers. Post-learning, queries the union
    of selected provider sets for users in this region.
    """
    # Determine provider set for this region
    providers_to_query: set[str] | None = set()
    for u in region_users:
        uid = u["user_id"]
        selected = await provider_learner.get_selected_providers(uid, geohash_key)
        if selected is None:
            # User is in learning phase — MUST query all providers
            providers_to_query = None
            break
        providers_to_query.update(selected)

    query_names = list(providers_to_query) if providers_to_query is not None else None

    # Build bounding box covering all users in this region
    boxes = []
    for u in region_users:
        loc = u["location"]
        radius = loc.get("radius_km", settings.default_radius_km)
        boxes.append(bounding_box(loc["latitude"], loc["longitude"], radius))

    merged_box = merge_bounding_boxes(boxes)
    center_lat = (merged_box[0] + merged_box[1]) / 2
    center_lon = (merged_box[2] + merged_box[3]) / 2

    diag_km = haversine(merged_box[0], merged_box[2], merged_box[1], merged_box[3])
    radius_nm = int(km_to_nautical_miles(diag_km / 2) + 10)
    radius_nm = min(radius_nm, 250)

    # Query providers in parallel
    aircraft_list, results_by_provider = await _provider_manager.query_providers(
        latitude=center_lat,
        longitude=center_lon,
        radius_nm=radius_nm,
        provider_names=query_names,
    )

    if not aircraft_list:
        return 0

    # Record cycle observations for per-user learning (silent, background)
    for u in region_users:
        uid = u["user_id"]
        loc = u["location"]
        await provider_learner.record_cycle_observation(
            user_id=uid,
            geohash=geohash_key,
            results_by_provider=results_by_provider,
            user_lat=loc["latitude"],
            user_lon=loc["longitude"],
            radius_km=loc.get("radius_km", settings.default_radius_km),
        )

    # Match against each user's watched preferences
    notification_count = 0
    for u in region_users:
        count = await _match_user_aircraft(u, aircraft_list, results_by_provider)
        notification_count += count

    return notification_count


async def _match_user_aircraft(
    user_data: dict,
    aircraft_list: list,
    results_by_provider: dict[str, list],
) -> int:
    """Check aircraft against user preferences and send notifications."""
    user_id = user_data["user_id"]
    loc = user_data["location"]
    prefs = user_data["preferences"]
    radius_km = loc.get("radius_km", settings.default_radius_km)

    selected_cats = prefs.get("selected_categories", [])
    disabled_types = set(prefs.get("disabled_types", []))
    custom_types = prefs.get("custom_aircraft", [])

    base_types = set()
    from app.aircraft.categories import AIRCRAFT_CATEGORIES
    for cat in selected_cats:
        for t in AIRCRAFT_CATEGORIES.get(cat, []):
            if t not in disabled_types:
                base_types.add(t)
    base_types.update(custom_types)

    watched_prefixes = resolve_match_prefixes(base_types)
    if not watched_prefixes:
        return 0

    user_lat = loc["latitude"]
    user_lon = loc["longitude"]
    count = 0

    for ac in aircraft_list:
        if not ac.has_position:
            continue

        ac_type = ac.aircraft_type.upper()
        if not ac_type or not any(ac_type.startswith(prefix) for prefix in watched_prefixes):
            continue

        is_inside, distance = is_within_square_and_circle(
            user_lat, user_lon, ac.latitude, ac.longitude, radius_km
        )
        if not is_inside:
            continue

        if await _is_in_cooldown(user_id, ac.icao24):
            continue

        # Generate unique notification ID for feedback tracking
        notification_id = str(uuid.uuid4())[:12]

        # Find which providers reported this aircraft
        reporting_providers = [
            pname for pname, plist in results_by_provider.items()
            if any(p.icao24 == ac.icao24 for p in plist)
        ]

        success = await send_aircraft_notification(
            user_id=user_id,
            aircraft=ac,
            distance_km=distance,
            notification_id=notification_id,
        )

        if success:
            await _set_cooldown(
                user_id=user_id,
                icao24=ac.icao24,
                notification_id=notification_id,
                aircraft_type=ac.aircraft_type,
                distance_km=distance,
                reporting_providers=reporting_providers,
            )
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


async def _set_cooldown(
    user_id: int,
    icao24: str,
    notification_id: str,
    aircraft_type: str,
    distance_km: float,
    reporting_providers: list[str],
) -> None:
    """Record notification history with feedback ID and cooldown."""
    now = datetime.now(timezone.utc)
    cooldown_until = now + timedelta(minutes=settings.cooldown_minutes)

    await notification_history_col().insert_one({
        "_id": notification_id,
        "user_id": user_id,
        "aircraft_icao24": icao24,
        "aircraft_type": aircraft_type,
        "distance_km": distance_km,
        "reporting_providers": reporting_providers,
        "notified_at": now,
        "cooldown_until": cooldown_until,
    })
