#!/usr/bin/env python3
"""Continuously refresh AGY's redacted project truth and publish findings."""
from __future__ import annotations

import logging
import time
from concurrent.futures import Future, ThreadPoolExecutor

from app import next_hour_shadow as next_hour_base
from app.agy_prediction_bridge import sync_findings_to_handoff
from app.agy_supervisor_bridge_v44 import build_supervisor_context_snapshot
from app.shadow_mongo_batch_v421 import update_next_hour_shadow, update_sentinel_shadow

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("plane_alerts.agy_bridge_daemon")

CONTEXT_INTERVAL_S = 30.0
HANDOFF_INTERVAL_S = 3.0
NEXT_HOUR_INTERVAL_S = 60.0
SENTINEL_INTERVAL_S = 120.0
SENTINEL_INITIAL_DELAY_S = 30.0
INDEX_RETRY_INTERVAL_S = 60.0
INDEX_REFRESH_INTERVAL_S = 3600.0


def _ensure_shadow_indexes() -> None:
    """Ensure private audit reads remain index-backed and bounded."""
    database = next_hour_base._db()
    if database is None:
        return
    database["flight_route_samples"].create_index("utc_date")
    database["prediction_sentinel_routes"].create_index("utc_date")
    database["prediction_lab_audit"].create_index([("kind", 1), ("status", 1), ("utc_date", 1)])
    database["prediction_lab_audit"].create_index([("kind", 1), ("status", 1), ("window_end", 1)])
    database["decision_records"].create_index([("subsystem", 1), ("timestamp", -1)])
    database["feedback"].create_index([("feedback_label", 1), ("updated_at", -1)])
    database["agy_shadow_reviews"].create_index("decision_id", unique=True)


def _finish_next_hour(future: Future[dict[str, int]]) -> None:
    counters = future.result()
    logger.info(
        "NEXT_HOUR_SHADOW created=%d resolved=%d unresolved=%d rejected=%d",
        counters.get("created", 0), counters.get("resolved", 0),
        counters.get("unresolved", 0), counters.get("rejected", 0),
    )


def _finish_sentinel(future: Future[dict[str, int]]) -> None:
    counters = future.result()
    logger.info(
        "EUROPE_SENTINEL_SHADOW adversarial=%d created=%d resolved=%d unresolved=%d",
        counters.get("adversarial", 0), counters.get("created", 0),
        counters.get("resolved", 0), counters.get("unresolved", 0),
    )


def main() -> int:
    started = time.monotonic()
    next_context = 0.0
    next_hour_audit = 0.0
    next_sentinel_audit = started + SENTINEL_INITIAL_DELAY_S
    next_index_refresh = 0.0
    next_hour_future: Future[dict[str, int]] | None = None
    sentinel_future: Future[dict[str, int]] | None = None

    # At most one point-heavy shadow audit runs at once. Context refresh and
    # finding handoff stay independent so AGY never competes with live alerts.
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="agy-shadow")
    logger.info("Plane Alerts v4.4 supervisor bridge daemon starting")

    try:
        while True:
            now = time.monotonic()

            if next_hour_future is not None and next_hour_future.done():
                try:
                    _finish_next_hour(next_hour_future)
                except Exception:
                    logger.exception("Next-hour shadow audit failed")
                next_hour_future = None
                next_hour_audit = now + NEXT_HOUR_INTERVAL_S

            if sentinel_future is not None and sentinel_future.done():
                try:
                    _finish_sentinel(sentinel_future)
                except Exception:
                    logger.exception("Europe sentinel shadow audit failed")
                sentinel_future = None
                next_sentinel_audit = now + SENTINEL_INTERVAL_S

            try:
                if now >= next_index_refresh:
                    _ensure_shadow_indexes()
                    next_index_refresh = now + INDEX_REFRESH_INTERVAL_S
                    logger.info("Supervisor/shadow indexes ready")
            except Exception:
                logger.exception("Supervisor index refresh failed")
                next_index_refresh = now + INDEX_RETRY_INTERVAL_S

            if next_hour_future is None and now >= next_hour_audit:
                next_hour_future = executor.submit(update_next_hour_shadow)
                next_hour_audit = float("inf")

            if sentinel_future is None and now >= next_sentinel_audit:
                sentinel_future = executor.submit(update_sentinel_shadow)
                next_sentinel_audit = float("inf")

            try:
                if now >= next_context:
                    build_supervisor_context_snapshot()
                    next_context = now + CONTEXT_INTERVAL_S
            except Exception:
                logger.exception("Plane Alerts supervisor context refresh failed")
                next_context = now + 10.0

            try:
                published = sync_findings_to_handoff()
                if published:
                    logger.info("Published %d AGY finding(s) to ChatGPT handoff", published)
            except Exception:
                logger.exception("AGY finding handoff failed")

            time.sleep(HANDOFF_INTERVAL_S)
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


if __name__ == "__main__":
    raise SystemExit(main())
