"""Plane Alerts v4.3 profile filtering integration.

Filtering is performed before the existing trajectory/CPA pipeline. The
existing matcher remains authoritative for geometry, route guards, lifecycle
and notifications. No database, AI, or network call is introduced here.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
import logging
from typing import Any

from app.aircraft.filtering import compile_filter
from app.worker import monitor, v35

logger = logging.getLogger(__name__)
_INSTALLED = False
_ORIGINAL_MATCH = monitor._match_user_aircraft
_ORIGINAL_COVERAGE_BOX = v35._coverage_box
_ORIGINAL_REGION_KEY = v35._region_key
_ORIGINAL_REGION_IS_HOT = v35._region_is_hot


def _maximum_radius(user: dict[str, Any]) -> float:
    location = user.get("location") or {}
    base = float(location.get("radius_km", monitor.settings.default_radius_km))
    compiled = compile_filter(user.get("preferences") or {})
    values = [base]
    for rule in [compiled.profile_rule, *compiled.category_rules.values(), *compiled.aircraft_rules.values()]:
        raw = rule.get("radius_km")
        if raw is None:
            continue
        try:
            values.append(max(1.0, min(150.0, float(raw))))
        except (TypeError, ValueError):
            continue
    return max(values)


def _user_with_poll_radius(user: dict[str, Any]) -> dict[str, Any]:
    clone = dict(user)
    location = dict(user.get("location") or {})
    location["radius_km"] = _maximum_radius(user)
    clone["location"] = location
    return clone


def _coverage_box_profiled(user: dict[str, Any]):
    # Shared polling must cover a larger per-category/per-aircraft radius even
    # though the base profile radius remains unchanged for other aircraft.
    return _ORIGINAL_COVERAGE_BOX(_user_with_poll_radius(user))


def _region_key_profiled(users: list[dict[str, Any]]) -> str:
    # Include effective polling radius in the cache key so changing an override
    # cannot briefly reuse a snapshot whose geographic footprint was smaller.
    return _ORIGINAL_REGION_KEY([_user_with_poll_radius(user) for user in users])


def _region_is_hot_profiled(region: v35.SharedPollRegion, aircraft: list[Any]) -> bool:
    clone = v35.SharedPollRegion(
        key=region.key,
        users=[_user_with_poll_radius(user) for user in region.users],
        latitude=region.latitude,
        longitude=region.longitude,
        radius_nm=region.radius_nm,
    )
    return _ORIGINAL_REGION_IS_HOT(clone, aircraft)


async def _match_user_aircraft_profiled(
    user: dict[str, Any],
    aircraft_list: list[Any],
    results_by_provider: dict[str, list[Any]],
) -> int:
    prefs = user.get("preferences") or {}
    compiled = compile_filter(prefs)
    if compiled.empty_selection:
        return 0

    location = user.get("location") or {}
    base_radius = float(location.get("radius_km", monitor.settings.default_radius_km))
    grouped: dict[float, list[Any]] = defaultdict(list)
    for aircraft in aircraft_list:
        if not getattr(aircraft, "has_position", False):
            continue
        decision = compiled.evaluate(aircraft, base_radius)
        if decision.matched:
            grouped[round(float(decision.radius_km), 4)].append(aircraft)

    if not grouped:
        return 0

    # The old matcher still contains the legacy selector. Feed it All Aircraft
    # only after v4.3 has already performed the real selection/rule evaluation;
    # this reuses every established trajectory and safety check unchanged.
    count = 0
    for radius, aircraft_group in grouped.items():
        scoped_user = dict(user)
        scoped_location = dict(location)
        scoped_location["radius_km"] = radius
        scoped_user["location"] = scoped_location
        scoped_prefs = deepcopy(prefs)
        scoped_prefs["selected_categories"] = ["All Aircraft"]
        scoped_prefs["disabled_types"] = []
        scoped_prefs["custom_aircraft"] = []
        scoped_user["preferences"] = scoped_prefs
        count += await _ORIGINAL_MATCH(scoped_user, aircraft_group, results_by_provider)
    return count


def install_profile_filter_guard_v43() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    monitor._match_user_aircraft = _match_user_aircraft_profiled
    v35._coverage_box = _coverage_box_profiled
    v35._region_key = _region_key_profiled
    v35._region_is_hot = _region_is_hot_profiled
    _INSTALLED = True
    logger.info("Plane Alerts v4.3 deterministic profile filter enabled")
