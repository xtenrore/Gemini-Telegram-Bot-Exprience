"""Admin dashboard API routes.

All endpoints are prefixed with ``/admin/api/`` by the FastAPI router.
The admin HTML/CSS/JS is served as static files at ``/admin``.
"""

from __future__ import annotations

import logging
import platform
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.aircraft.api_keys import opensky_key_manager
from app.config import settings
from app.database import (
    locations_col,
    notification_history_col,
    preferences_col,
    provider_coverage_col,
    users_col,
)
from app.worker.monitor import get_cycle_stats, get_provider_manager

logger = logging.getLogger(__name__)

admin_router = APIRouter(tags=["admin"])

# Optional HTTP Basic auth
_security = HTTPBasic(auto_error=False)
_start_time = time.time()


async def _check_auth(
    credentials: HTTPBasicCredentials | None = Depends(_security),
) -> None:
    """Check admin password if one is configured."""
    if not settings.admin_password:
        return  # No password set — rely on Caddy/firewall
    if credentials is None or credentials.password != settings.admin_password:
        raise HTTPException(
            status_code=401,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Basic"},
        )


# ═══════════════════════════════════════════════════════════════════════════
# OVERVIEW
# ═══════════════════════════════════════════════════════════════════════════

@admin_router.get("/overview")
async def admin_overview(_: None = Depends(_check_auth)) -> dict[str, Any]:
    """High-level system overview."""
    total_users = await users_col().count_documents({})
    active_users = await users_col().count_documents({"setup_complete": True})
    total_notifications = await notification_history_col().count_documents({})

    cycle_stats = get_cycle_stats()

    # Memory usage (rough estimate)
    try:
        import psutil  # type: ignore[import-untyped]
        process = psutil.Process()
        mem_mb = process.memory_info().rss / (1024 * 1024)
    except ImportError:
        mem_mb = -1

    return {
        "total_users": total_users,
        "active_users": active_users,
        "total_notifications": total_notifications,
        "uptime_seconds": round(time.time() - _start_time, 1),
        "memory_mb": round(mem_mb, 1),
        "python_version": platform.python_version(),
        "cycle_stats": cycle_stats,
    }


# ═══════════════════════════════════════════════════════════════════════════
# USERS
# ═══════════════════════════════════════════════════════════════════════════

@admin_router.get("/users")
async def admin_users(_: None = Depends(_check_auth)) -> list[dict[str, Any]]:
    """List all registered users with their locations and preferences."""
    cursor = users_col().find({}).sort("created_at", -1)
    users = []
    async for doc in cursor:
        user_id = doc["user_id"]

        loc = await locations_col().find_one({"user_id": user_id})
        prefs = await preferences_col().find_one({"user_id": user_id})

        users.append({
            "user_id": user_id,
            "username": doc.get("username", ""),
            "first_name": doc.get("first_name", ""),
            "setup_complete": doc.get("setup_complete", False),
            "terms_accepted": doc.get("terms_accepted", False),
            "created_at": str(doc.get("created_at", "")),
            "last_active": str(doc.get("last_active", "")),
            "location": {
                "latitude": loc.get("latitude") if loc else None,
                "longitude": loc.get("longitude") if loc else None,
                "geohash": loc.get("geohash", "") if loc else "",
                "radius_km": loc.get("radius_km", settings.default_radius_km) if loc else settings.default_radius_km,
            },
            "preferences": {
                "selected_categories": prefs.get("selected_categories", []) if prefs else [],
                "custom_aircraft": prefs.get("custom_aircraft", []) if prefs else [],
            },
        })

    return users


@admin_router.post("/user/{user_id}/toggle")
async def admin_toggle_user(
    user_id: int,
    _: None = Depends(_check_auth),
) -> dict[str, Any]:
    """Toggle a user's setup_complete status (enable/disable monitoring)."""
    doc = await users_col().find_one({"user_id": user_id})
    if not doc:
        raise HTTPException(status_code=404, detail="User not found")

    new_status = not doc.get("setup_complete", False)
    await users_col().update_one(
        {"user_id": user_id},
        {"$set": {"setup_complete": new_status}},
    )

    return {"user_id": user_id, "setup_complete": new_status}


# ═══════════════════════════════════════════════════════════════════════════
# PROVIDERS
# ═══════════════════════════════════════════════════════════════════════════

@admin_router.get("/providers")
async def admin_providers(_: None = Depends(_check_auth)) -> dict[str, Any]:
    """Provider health, request counts, and coverage scores."""
    pm = get_provider_manager()
    return {
        "providers": pm.get_all_provider_status(),
        "coverage": pm.get_coverage_summary(),
    }


# ═══════════════════════════════════════════════════════════════════════════
# API KEYS
# ═══════════════════════════════════════════════════════════════════════════

@admin_router.get("/keys")
async def admin_keys(_: None = Depends(_check_auth)) -> dict[str, Any]:
    """OpenSky API key rotation status."""
    status = opensky_key_manager.get_status()
    return {
        "total_keys": status.total_keys,
        "active_key_index": status.active_key_index,
        "all_exhausted": status.all_exhausted,
        "keys": status.keys,
    }


# ═══════════════════════════════════════════════════════════════════════════
# NOTIFICATIONS
# ═══════════════════════════════════════════════════════════════════════════

@admin_router.get("/notifications")
async def admin_notifications(
    limit: int = Query(default=50, le=200),
    _: None = Depends(_check_auth),
) -> list[dict[str, Any]]:
    """Recent notification history."""
    cursor = (
        notification_history_col()
        .find({})
        .sort("notified_at", -1)
        .limit(limit)
    )

    results = []
    async for doc in cursor:
        results.append({
            "user_id": doc.get("user_id"),
            "aircraft_icao24": doc.get("aircraft_icao24", ""),
            "notified_at": str(doc.get("notified_at", "")),
            "cooldown_until": str(doc.get("cooldown_until", "")),
        })

    return results


# ═══════════════════════════════════════════════════════════════════════════
# SYSTEM
# ═══════════════════════════════════════════════════════════════════════════

@admin_router.get("/system")
async def admin_system(_: None = Depends(_check_auth)) -> dict[str, Any]:
    """System info: worker stats, database stats, platform info."""
    cycle_stats = get_cycle_stats()

    # Database stats
    try:
        db_stats = {
            "users": await users_col().count_documents({}),
            "locations": await locations_col().count_documents({}),
            "preferences": await preferences_col().count_documents({}),
            "notifications": await notification_history_col().count_documents({}),
            "coverage_regions": await provider_coverage_col().count_documents({}),
        }
    except Exception:
        db_stats = {"error": "Could not fetch DB stats"}

    return {
        "platform": platform.platform(),
        "python_version": platform.python_version(),
        "uptime_seconds": round(time.time() - _start_time, 1),
        "worker": cycle_stats,
        "database": db_stats,
        "config": {
            "poll_interval_seconds": settings.poll_interval_seconds,
            "default_radius_km": settings.default_radius_km,
            "cooldown_minutes": settings.cooldown_minutes,
        },
    }
