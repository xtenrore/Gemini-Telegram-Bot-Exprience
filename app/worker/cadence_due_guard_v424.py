"""Plane Alerts v4.2.4 five-second due-time jitter guard.

The main scheduler aims to start monitor cycles every five seconds, but normal
async/database scheduling jitter can make a region evaluation begin a fraction
of a second early. A strict >= 5.000s per-user gate can then defer that user for
an entire extra scheduler cycle, turning an intended five-second check into a
roughly ten-second gap.

This guard adds a small bounded scheduling tolerance. It does not make shared
ADS-B provider refreshes more frequent than their existing hot five-second
cadence; it only prevents sub-cycle timing jitter from skipping a due user.
"""
from __future__ import annotations

from typing import Any

from app.worker import v36

_DUE_EARLY_TOLERANCE_S = 0.25
_INSTALLED = False


def _is_due_jitter_safe(user: dict[str, Any], now_mono: float) -> bool:
    uid = int(user["user_id"])
    previous = v36._last_user_processed_mono.get(uid)
    if previous is None:
        return True

    delay_s = float(v36._control(user)["delay_seconds"])
    # Never advance a configured cadence by more than 250 ms, and scale the
    # tolerance down for any future sub-five-second setting.
    tolerance_s = min(_DUE_EARLY_TOLERANCE_S, max(0.0, delay_s * 0.05))
    return (float(now_mono) - float(previous)) >= (delay_s - tolerance_s)


def install_cadence_due_guard_v424() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    v36._is_due = _is_due_jitter_safe
    _INSTALLED = True
