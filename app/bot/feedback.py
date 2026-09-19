"""Plane Alerts notification feedback with v4.4 forensic linkage.

Feedback is first-class evidence. The live callback never waits for an AI
provider: it links the response to the exact notification/Decision Recorder
snapshot and schedules any provider relearning as non-critical background work.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from telegram import Update
from telegram.constants import ParseMode

from app.aircraft.learner import provider_learner
from app.bot.keyboards import CB_FB_DISLIKE_PREFIX, CB_FB_LIKE_PREFIX
from app.database import feedback_col, locations_col, notification_history_col
from app.decision_recorder import record_decision

logger = logging.getLogger(__name__)


async def handle_feedback_callback(update: Update) -> None:
    query = update.callback_query
    if query is None or query.data is None:
        return
    await query.answer()  # Acknowledge Telegram immediately.
    user = update.effective_user
    if user is None:
        return

    data = query.data
    if data.startswith(CB_FB_LIKE_PREFIX):
        await _record_feedback(query, user.id, data[len(CB_FB_LIKE_PREFIX):], "like")
    elif data.startswith(CB_FB_DISLIKE_PREFIX):
        await _record_feedback(query, user.id, data[len(CB_FB_DISLIKE_PREFIX):], "dislike")


async def _notification(user_id: int, notif_id: str) -> dict[str, Any]:
    return (
        await notification_history_col().find_one({"_id": notif_id, "user_id": int(user_id)})
        or await notification_history_col().find_one({"user_id": int(user_id), "aircraft_icao24": notif_id})
        or {}
    )


async def _record_feedback(query: Any, user_id: int, notif_id: str, feedback: str) -> None:
    notif = await _notification(user_id, notif_id)
    linked_decision = str(notif.get("decision_record_id") or "")
    label = "helpful" if feedback == "like" else "non_helpful"
    now = datetime.now(timezone.utc)

    await feedback_col().update_one(
        {"user_id": int(user_id), "notification_id": notif_id},
        {"$set": {
            "user_id": int(user_id),
            "notification_id": notif_id,
            "feedback": feedback,  # Backward-compatible value.
            "feedback_label": label,
            "decision_record_id": linked_decision or None,
            "aircraft_icao24": notif.get("aircraft_icao24"),
            "aircraft_type": notif.get("aircraft_type"),
            "projected_closest_km": notif.get("projected_closest_km"),
            "observed_closest_km": notif.get("observed_closest_km"),
            "prediction_confidence": notif.get("prediction_confidence"),
            "trajectory_state": notif.get("trajectory_state"),
            "updated_at": now,
        }},
        upsert=True,
    )

    feedback_decision = record_decision(
        subsystem="user_feedback",
        event=label,
        state_key=f"feedback:{int(user_id)}:{notif_id}",
        state=label,
        decision=label,
        reason_code="user_marked_non_helpful" if feedback == "dislike" else "user_marked_helpful",
        user_id=int(user_id),
        notification_id=notif_id,
        force=True,
        evidence={
            "linked_decision_record_id": linked_decision or None,
            "flight_number": notif.get("callsign"),
            "icao24": notif.get("aircraft_icao24"),
            "aircraft_type": notif.get("aircraft_type"),
            "projected_closest_km": notif.get("projected_closest_km"),
            "observed_closest_km": notif.get("observed_closest_km"),
            "prediction_confidence": notif.get("prediction_confidence"),
            "trajectory_state": notif.get("trajectory_state"),
            "notified_at": notif.get("notified_at"),
        },
    )
    if feedback_decision:
        await feedback_col().update_one(
            {"user_id": int(user_id), "notification_id": notif_id},
            {"$set": {"feedback_decision_record_id": feedback_decision}},
        )

    if feedback == "dislike":
        # Relearning is useful evidence collection, but it is never allowed to
        # hold the Telegram callback open or become alert-decision authority.
        loc = await locations_col().find_one({"user_id": int(user_id)}, {"geohash": 1})
        geohash = str((loc or {}).get("geohash") or "")
        if geohash:
            async def relearn() -> None:
                try:
                    await provider_learner.trigger_relearning(
                        user_id=int(user_id), geohash=geohash, extra_planes=25
                    )
                except Exception:
                    logger.exception("feedback_relearning_failed user=%s", user_id)
            asyncio.create_task(relearn(), name=f"feedback-relearn:{user_id}")

    if query.message:
        text = (
            "Feedback recorded. This result is linked to the decision evidence for reliability review."
            if feedback == "dislike"
            else "Thank you. This successful result will also be retained as calibration evidence."
        )
        await query.message.reply_text(text, parse_mode=ParseMode.HTML)


# Backward-compatible helpers retained for tests/callers.
async def _on_like(query: Any, user_id: int, notif_id: str) -> None:
    await _record_feedback(query, user_id, notif_id, "like")


async def _on_dislike(query: Any, user_id: int, notif_id: str) -> None:
    await _record_feedback(query, user_id, notif_id, "dislike")
