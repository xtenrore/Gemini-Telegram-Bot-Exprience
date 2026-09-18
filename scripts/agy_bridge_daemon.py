#!/usr/bin/env python3
"""Continuously refresh AGY's redacted truth and publish findings to ChatGPT."""
from __future__ import annotations

import logging
import time

from app.agy_prediction_bridge import build_context_snapshot, sync_findings_to_handoff

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("plane_alerts.agy_bridge_daemon")

CONTEXT_INTERVAL_S = 30.0
HANDOFF_INTERVAL_S = 3.0


def main() -> int:
    next_context = 0.0
    logger.info("Prediction Lab bridge daemon starting")
    while True:
        now = time.monotonic()
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
