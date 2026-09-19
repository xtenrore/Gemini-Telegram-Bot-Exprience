"""Plane Alerts v4.4 production composition layer.

The existing v4.3 main lifecycle remains the proven source of monitoring,
Telegram, Prediction Lab and shutdown behavior. This module composes v4.4-only
routes/handler registration around it so the release does not duplicate or
rewrite the production startup sequence.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

import app.main as base
from app.bot.profile_modes_v44 import register_profile_mode_handlers_v44
from app.profile_guided_sync_v44 import install_profile_guided_sync_v44
from app.profile_miniapp import router as profile_miniapp_router
from app.profile_miniapp_entry import router as profile_miniapp_entry_router

# Register v4.4 entry handlers immediately before the existing profile router.
_original_profile_register = base.register_profile_handlers


def _register_profiles_v44(application) -> None:
    register_profile_mode_handlers_v44(application)
    _original_profile_register(application)


base.register_profile_handlers = _register_profiles_v44

# Photography handlers resolve their module globals at runtime. Install the
# profile-sync wrappers immediately before the proven handler set is registered.
_original_photo_register = base.register_photography_handlers


def _register_photography_v44(application) -> None:
    install_profile_guided_sync_v44()
    _original_photo_register(application)


base.register_photography_handlers = _register_photography_v44

app = base.app
app.title = "Plane Alerts"
app.description = (
    "Deterministic real-time ADS-B spotting intelligence with v4.4 transient-turn protection, "
    "bounded decision forensics, Visual Setup profiles, and project-wide reliability supervision"
)
app.version = "4.4.0"

# Public Mini App routes are deliberately outside /admin. Every API mutation is
# authorized independently with Telegram initData and profile ownership checks.
app.include_router(profile_miniapp_router, tags=["profile-setup-v4.4"])
app.include_router(profile_miniapp_entry_router, tags=["profile-setup-v4.4"])


def _upgrade_route(path: str, transform: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
    """Wrap an existing JSON endpoint while preserving its dependencies/router."""
    for route in app.routes:
        if getattr(route, "path", None) != path:
            continue
        old = getattr(route, "endpoint", None)
        if old is None:
            continue

        async def endpoint(*args, __old=old, __transform=transform, **kwargs):
            result = await __old(*args, **kwargs)
            if isinstance(result, dict):
                return __transform(result)
            return result

        route.endpoint = endpoint
        dependant = getattr(route, "dependant", None)
        if dependant is not None:
            dependant.call = endpoint
        break


def _health_v44(payload: dict[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    result["version"] = "4.4.0"
    worker = dict(result.get("worker") or {})
    worker["version"] = "4.4.0"
    result["worker"] = worker
    intelligence = dict(result.get("spotting_intelligence") or {})
    intelligence.update({
        "transient_turn_protection": True,
        "decision_recorder": True,
        "google_contrails_cached": True,
        "agy_project_supervisor": True,
        "agy_shadow_review_authoritative": False,
        "guided_setup": True,
        "visual_setup": True,
        "telegram_mini_app": True,
    })
    result["spotting_intelligence"] = intelligence
    return result


def _stats_v44(payload: dict[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    result["version"] = "4.4.0"
    return result


_upgrade_route("/health", _health_v44)
_upgrade_route("/stats", _stats_v44)
