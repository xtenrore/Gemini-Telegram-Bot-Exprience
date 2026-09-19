"""Bound route-history background writes so they cannot occupy all task slots.

Route samples are helpful telemetry, but they are never allowed to delay or
starve the live alert path. Production logs showed the v2 background task pool
stuck at its 24-task cap for repeated cycles. This wrapper bounds each original
Mongo route-sample write; a timed-out sample is safely dropped and a later
cycle can record another point.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.intelligence import route_guard_v2 as v2

logger = logging.getLogger(__name__)

_OBSERVE_TIMEOUT_S = 3.0
_BASE_OBSERVE = v2._ORIGINAL_OBSERVE
_INSTALLED = False


async def _bounded_original_observe(self: Any, ac: Any, *, now: float | None = None) -> None:
    try:
        await asyncio.wait_for(
            _BASE_OBSERVE(self, ac, now=now),
            timeout=_OBSERVE_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "flight_route_observe_timeout callsign=%s timeout_s=%.1f",
            getattr(ac, "callsign", ""),
            _OBSERVE_TIMEOUT_S,
        )


def install_route_observe_guard_v44() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    # route_guard_v2.observe_nonblocking resolves this module-level callable at
    # execution time, so replacing it here bounds every background write while
    # preserving v2's per-callsign dedupe and task cleanup callback.
    v2._ORIGINAL_OBSERVE = _bounded_original_observe
    _INSTALLED = True
