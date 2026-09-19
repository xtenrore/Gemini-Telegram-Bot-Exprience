"""Quality gate for next-hour Prediction Lab shadows.

The base evaluator remains shadow-only. This wrapper prevents single-day and
high-variance/multimodal historical timing from being treated as a usable
next-hour expectation. Missing ADS-B coverage remains unresolved, never success
or failure.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app import next_hour_shadow as base

MIN_HISTORY_DAYS = 2
MAX_USABLE_TIME_SPREAD_S = base.MAX_WINDOW_HALF_S


def _history_quality_reason(days: int, spread_s: float) -> str:
    if int(days) < MIN_HISTORY_DAYS:
        return "insufficient_history_days"
    if float(spread_s) > MAX_USABLE_TIME_SPREAD_S:
        return "ambiguous_or_multimodal_time_history"
    return ""


def update_next_hour_shadow(now: datetime | None = None) -> dict[str, int]:
    effective_now = now or datetime.now(timezone.utc)
    counters = dict(base.update_next_hour_shadow(effective_now))
    counters.setdefault("rejected", 0)

    database = base._db()
    if database is None:
        return counters

    audit = database["prediction_lab_audit"]
    today = effective_now.date().isoformat()
    cursor = audit.find(
        {
            "kind": "next_hour_expectation",
            "utc_date": today,
            "status": "awaiting_outcome",
            "coverage_mode": "historical_flight_number_timing_shadow",
        },
        {
            "_id": 1,
            "historical_days": 1,
            "historical_time_spread_s": 1,
        },
    )

    for expectation in cursor:
        reason = _history_quality_reason(
            int(expectation.get("historical_days") or 0),
            float(expectation.get("historical_time_spread_s") or 0.0),
        )
        if not reason:
            continue
        result = audit.update_one(
            {"_id": expectation["_id"], "status": "awaiting_outcome"},
            {
                "$set": {
                    "status": "rejected_history_quality",
                    "shadow_quality_reason": reason,
                    "resolved_at": effective_now,
                    "note": (
                        "Shadow-only expectation rejected before scoring because "
                        "historical timing is not repeatable enough."
                    ),
                }
            },
        )
        counters["rejected"] += int(result.modified_count or 0)

    return counters
