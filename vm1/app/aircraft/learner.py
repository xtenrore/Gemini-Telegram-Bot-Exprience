"""Per-user aircraft provider learning and selection engine.

Strategy:
1. Learning Phase (first 100 unique aircraft detected in user's area):
   - All 5 providers are queried every monitoring cycle.
   - Learning happens silently in the background on ALL detected aircraft
     within the user's monitoring radius (not limited to user's selected types).
   - Tracks which providers report each aircraft and which miss it.
   - When providers disagree, the AI Judge is consulted to identify false reports.

2. Provider Selection (after target plane count reached):
   - Best provider: Provider with highest total planes found.
   - Reliable providers: Providers with zero false reports.
   - Final selected set = {best_provider} ∪ {reliable_providers}.
   - Post-learning cycles only query the selected set for maximum efficiency.

3. Re-learning & Location Reset:
   - When a user changes location (/location), learning auto-resets for the new region.
   - When a user submits negative feedback (dislike button), an incremental
     re-learning round (+25 test planes) is triggered.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.aircraft.ai_judge import ai_judge
from app.aircraft.models import NormalizedAircraft
from app.config import settings
from app.database import provider_learning_col
from app.worker.geo import haversine

logger = logging.getLogger(__name__)


class ProviderLearner:
    """Manages per-user, per-location provider learning and selection."""

    async def get_selected_providers(
        self, user_id: int, geohash: str
    ) -> list[str] | None:
        """Return the list of provider names to query for this user.

        Returns ``None`` if the user is in the learning phase (meaning ALL
        providers should be queried).
        """
        doc = await provider_learning_col().find_one(
            {"user_id": user_id, "geohash": geohash}
        )
        if not doc or not doc.get("is_learning_complete", False):
            return None

        selected = doc.get("selected_providers", [])
        return selected if selected else None

    async def record_cycle_observation(
        self,
        user_id: int,
        geohash: str,
        results_by_provider: dict[str, list[NormalizedAircraft]],
        user_lat: float,
        user_lon: float,
        radius_km: float,
    ) -> None:
        """Process aircraft observations from all providers for a single cycle.

        Updates per-provider statistics, tracks unique aircraft observed, and
        finalises selection when the target plane count is reached.
        """
        # Load or create learning record
        doc = await provider_learning_col().find_one(
            {"user_id": user_id, "geohash": geohash}
        )

        target_count = settings.learning_plane_threshold
        if doc:
            if doc.get("is_learning_complete", False):
                return  # Learning already completed
            target_count = doc.get("target_plane_count", settings.learning_plane_threshold)
            seen_icao24s = set(doc.get("seen_icao24s", []))
            provider_stats = doc.get("provider_stats", {})
            planes_observed = doc.get("planes_observed", 0)
        else:
            seen_icao24s = set()
            provider_stats = {}
            planes_observed = 0

        # Ensure stats dict has all active providers
        all_provider_names = list(results_by_provider.keys())
        for pname in all_provider_names:
            if pname not in provider_stats:
                provider_stats[pname] = {
                    "planes_found": 0,
                    "planes_missed": 0,
                    "false_reports": 0,
                }

        # Build map of icao24 -> list of provider names that reported it
        plane_map: dict[str, list[str]] = {}
        plane_obj_map: dict[str, NormalizedAircraft] = {}

        for pname, aircraft_list in results_by_provider.items():
            for ac in aircraft_list:
                if not ac.has_position:
                    continue
                # Must be within user radius
                dist = haversine(user_lat, user_lon, ac.latitude, ac.longitude)
                if dist > radius_km:
                    continue

                icao = ac.icao24
                if icao not in plane_map:
                    plane_map[icao] = []
                    plane_obj_map[icao] = ac
                plane_map[icao].append(pname)

        if not plane_map:
            return

        # Process each plane observed in this cycle
        new_planes_count = 0
        for icao, reporting in plane_map.items():
            missing = [p for p in all_provider_names if p not in reporting]
            ac = plane_obj_map[icao]

            # Check AI only for suspicious single-provider detections
            if len(reporting) == 1 and len(missing) >= 2 and ai_judge.can_call():
                try:
                    verdict = await ai_judge.judge_conflict(
                        icao24=icao,
                        aircraft_type=ac.aircraft_type,
                        lat=ac.latitude,
                        lon=ac.longitude,
                        providers_reporting=reporting,
                        providers_missing=missing,
                        user_lat=user_lat,
                        user_lon=user_lon,
                        radius_km=radius_km,
                    )
                    if verdict == "FALSE":
                        # Providers reporting a false detection get penalized
                        for pname in reporting:
                            provider_stats[pname]["false_reports"] += 1
                        continue  # Skip counting false detection as a valid plane
                except Exception as exc:
                    logger.debug("AI conflict resolution skipped: %s", exc)

            # Credit reporting providers
            for pname in reporting:
                provider_stats[pname]["planes_found"] += 1
            for pname in missing:
                provider_stats[pname]["planes_missed"] += 1

            if icao not in seen_icao24s:
                seen_icao24s.add(icao)
                new_planes_count += 1

        planes_observed += new_planes_count
        is_complete = planes_observed >= target_count
        selected_providers: list[str] = []

        if is_complete:
            selected_providers = self._finalize_selection(provider_stats, all_provider_names)
            logger.info(
                "User %d learning complete for region %s (%d planes observed). "
                "Selected providers: %s",
                user_id,
                geohash,
                planes_observed,
                selected_providers,
            )

        # Update database record
        await provider_learning_col().update_one(
            {"user_id": user_id, "geohash": geohash},
            {
                "$set": {
                    "user_id": user_id,
                    "geohash": geohash,
                    "planes_observed": planes_observed,
                    "target_plane_count": target_count,
                    "is_learning_complete": is_complete,
                    "seen_icao24s": list(seen_icao24s),
                    "provider_stats": provider_stats,
                    "selected_providers": selected_providers,
                    "updated_at": datetime.now(timezone.utc),
                }
            },
            upsert=True,
        )

    def _finalize_selection(
        self, provider_stats: dict[str, dict[str, int]], all_provider_names: list[str]
    ) -> list[str]:
        """Select best provider + all reliable providers (zero false reports)."""
        if not provider_stats:
            return all_provider_names

        # Find best provider by total planes found
        best_provider = max(
            provider_stats.keys(),
            key=lambda p: provider_stats[p].get("planes_found", 0),
        )

        # Find reliable providers (false_reports == 0)
        reliable = [
            p for p, stats in provider_stats.items()
            if stats.get("false_reports", 0) == 0
        ]

        selected = set(reliable)
        selected.add(best_provider)

        # Fall back to all providers if selection ends up empty
        result = [p for p in all_provider_names if p in selected]
        return result if result else all_provider_names

    async def trigger_relearning(
        self, user_id: int, geohash: str, extra_planes: int = 25
    ) -> None:
        """Trigger an incremental re-learning round (e.g. after negative feedback).

        Adds *extra_planes* to the target plane count and sets learning as incomplete.
        """
        doc = await provider_learning_col().find_one(
            {"user_id": user_id, "geohash": geohash}
        )

        current_observed = doc.get("planes_observed", 0) if doc else 0
        new_target = current_observed + extra_planes

        await provider_learning_col().update_one(
            {"user_id": user_id, "geohash": geohash},
            {
                "$set": {
                    "is_learning_complete": False,
                    "target_plane_count": new_target,
                    "updated_at": datetime.now(timezone.utc),
                }
            },
            upsert=True,
        )
        logger.info(
            "User %d re-learning triggered for region %s (+%d planes, new target: %d)",
            user_id,
            geohash,
            extra_planes,
            new_target,
        )

    async def check_and_handle_location_change(
        self, user_id: int, old_geohash: str, new_geohash: str
    ) -> None:
        """Auto-detect location change and reset learning for the new region."""
        if old_geohash == new_geohash:
            return

        logger.info(
            "User %d changed location from geohash %s -> %s. Initiating fresh learning.",
            user_id,
            old_geohash,
            new_geohash,
        )
        # Note: We keep the old geohash record in DB in case the user returns to it,
        # but the new geohash will start its own fresh learning flow automatically.

    async def get_user_learning_status(self, user_id: int, geohash: str) -> dict[str, Any]:
        """Return learning status summary for user status / admin dashboard."""
        doc = await provider_learning_col().find_one(
            {"user_id": user_id, "geohash": geohash}
        )
        if not doc:
            return {
                "in_progress": True,
                "planes_observed": 0,
                "target_plane_count": settings.learning_plane_threshold,
                "progress_pct": 0.0,
                "selected_providers": [],
            }

        observed = doc.get("planes_observed", 0)
        target = doc.get("target_plane_count", settings.learning_plane_threshold)
        pct = min(100.0, round((observed / max(1, target)) * 100, 1))

        return {
            "in_progress": not doc.get("is_learning_complete", False),
            "planes_observed": observed,
            "target_plane_count": target,
            "progress_pct": pct,
            "selected_providers": doc.get("selected_providers", []),
            "provider_stats": doc.get("provider_stats", {}),
        }


# Singleton learner instance
provider_learner = ProviderLearner()
