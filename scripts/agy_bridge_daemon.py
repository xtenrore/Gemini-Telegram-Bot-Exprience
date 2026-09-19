#!/usr/bin/env python3
"""Continuously refresh AGY's redacted truth and publish findings to ChatGPT."""
from __future__ import annotations

import logging
import time

from app.agy_prediction_bridge import build_context_snapshot, sync_findings_to_handoff
from app.agy_shadow_runner import ShadowAuditEvent, SingleFlightShadowAudits
from app.next_hour_shadow_v43 import update_next_hour_shadow
from app.sentinel_shadow import update_sentinel_shadow

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("plane_alerts.agy_bridge_daemon")

CONTEXT_INTERVAL_S = 30.0
HANDOFF_INTERVAL_S = 3.0
NEXT_HOUR_INTERVAL_S = 60.0
SENTINEL_INTERVAL_S = 120.0


def _log_shadow_event(event: ShadowAuditEvent) -> None:
    if not event.ok:
        logger.warning(
            "%s shadow audit failed outside handoff loop error=%s detail=%s",
            event.kind,
            event.error_type or "unknown",
            event.error_message or "none",
        )
        return

    counters = event.counters
    if event.kind == "next_hour":
        logger.info(
            "NEXT_HOUR_SHADOW created=%d resolved=%d unresolved=%d rejected=%d",
            counters.get("created", 0),
            counters.get("resolved", 0),
            counters.get("unresolved", 0),
            counters.get("rejected", 0),
        )
    else:
        logger.info(
            "EUROPE_SENTINEL_SHADOW adversarial=%d created=%d resolved=%d unresolved=%d",
            counters.get("adversarial", 0),
            counters.get("created", 0),
            counters.get("resolved", 0),
            counters.get("unresolved", 0),
        )


def main() -> int:
    next_context = 0.0
    shadow = SingleFlightShadowAudits(
        next_hour_fn=update_next_hour_shadow,
        sentinel_fn=update_sentinel_shadow,
        next_hour_interval_s=NEXT_HOUR_INTERVAL_S,
        sentinel_interval_s=SENTINEL_INTERVAL_S,
    )
    logger.info("Prediction Lab bridge daemon starting")
    try:
        while True:
            now = time.monotonic()

            # Context + finding publication are the bridge's primary duties.
            # They run before any shadow scheduling so slow non-critical audits
            # can never delay the 3-second ChatGPT handoff cadence.
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

            for event in shadow.poll(now):
                _log_shadow_event(event)

            time.sleep(HANDOFF_INTERVAL_S)
    finally:
        shadow.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
