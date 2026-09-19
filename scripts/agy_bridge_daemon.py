#!/usr/bin/env python3
"""Continuously refresh AGY's redacted truth and publish findings to ChatGPT."""
from __future__ import annotations

import logging
import time

from app import next_hour_shadow as next_hour_base
from app.agy_prediction_bridge import build_context_snapshot, sync_findings_to_handoff
from app.next_hour_shadow_v43 import update_next_hour_shadow
from app.sentinel_shadow import update_sentinel_shadow

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
    """Ensure the read patterns used by private shadow audits are index-backed."""
    database = next_hour_base._db()
    if database is None:
        return
    database["flight_route_samples"].create_index("utc_date")
    database["prediction_sentinel_routes"].create_index("utc_date")
    database["prediction_lab_audit"].create_index(
        [("kind", 1), ("status", 1), ("utc_date", 1)]
    )
    database["prediction_lab_audit"].create_index(
        [("kind", 1), ("status", 1), ("window_end", 1)]
    )


def main() -> int:
    started = time.monotonic()
    next_context = 0.0
    next_hour_audit = 0.0
    # Avoid launching both heavyweight route scans back-to-back at container
    # startup. Context refresh and Next60 run first; Europe sentinel follows.
    next_sentinel_audit = started + SENTINEL_INITIAL_DELAY_S
    next_index_refresh = 0.0
    logger.info("Prediction Lab bridge daemon starting")
    while True:
        now = time.monotonic()
        try:
            if now >= next_index_refresh:
                _ensure_shadow_indexes()
                next_index_refresh = now + INDEX_REFRESH_INTERVAL_S
                logger.info("Prediction Lab shadow indexes ready")
        except Exception:
            # Index creation is an optimization, never a reason to stop the
            # bridge. Retry slowly so a Mongo outage is not amplified.
            logger.exception("Prediction Lab shadow index refresh failed")
            next_index_refresh = now + INDEX_RETRY_INTERVAL_S
        try:
            if now >= next_hour_audit:
                counters = update_next_hour_shadow()
                next_hour_audit = now + NEXT_HOUR_INTERVAL_S
                logger.info(
                    "NEXT_HOUR_SHADOW created=%d resolved=%d unresolved=%d rejected=%d",
                    counters.get("created", 0),
                    counters.get("resolved", 0),
                    counters.get("unresolved", 0),
                    counters.get("rejected", 0),
                )
        except Exception:
            logger.exception("Next-hour shadow audit failed")
            # A failed Mongo scan must not be retried more aggressively than a
            # healthy scan; rapid retries can make a transient outage worse.
            next_hour_audit = now + NEXT_HOUR_INTERVAL_S
        try:
            if now >= next_sentinel_audit:
                counters = update_sentinel_shadow()
                next_sentinel_audit = now + SENTINEL_INTERVAL_S
                logger.info(
                    "EUROPE_SENTINEL_SHADOW adversarial=%d created=%d resolved=%d unresolved=%d",
                    counters.get("adversarial", 0),
                    counters.get("created", 0),
                    counters.get("resolved", 0),
                    counters.get("unresolved", 0),
                )
        except Exception:
            logger.exception("Europe sentinel shadow audit failed")
            next_sentinel_audit = now + SENTINEL_INTERVAL_S
        try:
            if now >= next_context:
                build_context_snapshot()
                next_context = now + CONTEXT_INTERVAL_S
        except Exception:
            logger.exception("Prediction Lab context refresh failed")
            next_context = now + 10.0
        try:
            published = sync_findings_to_handoff()
            if published:
                logger.info("Published %d AGY finding(s) to ChatGPT handoff", published)
        except Exception:
            logger.exception("Prediction Lab finding handoff failed")
        time.sleep(HANDOFF_INTERVAL_S)


if __name__ == "__main__":
    raise SystemExit(main())
