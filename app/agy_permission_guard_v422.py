"""Permission-denial recovery for the Plane Alerts AGY goal supervisor.

Headless Antigravity correctly refuses shell constructs that are outside the
explicit allowlist. A denied tool call must not be mistaken for a successful
goal cycle just because the CLI exits with code 0. This guard keeps the strict
permissions and schedules a bounded retry instead of widening shell access.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from app import agy_worker as base

logger = logging.getLogger("plane_alerts.agy_permission_guard_v422")

_ORIGINAL_RUN_GOAL = base.GoalSupervisor._run_goal
_INSTALLED = False
_PERMISSION_RETRY_BASE_S = 15
_PERMISSION_RETRY_MAX_S = 300

_DENIAL_MARKERS = (
    "permission check failed",
    'required the "command" permission',
    '"denied_actions":[',
    '"denied_actions": [{',
)


def output_has_permission_denial(lines: list[str] | tuple[str, ...]) -> bool:
    """Return True when streamed AGY output contains a real denied tool action."""
    for line in lines:
        lowered = str(line).lower()
        if any(marker in lowered for marker in _DENIAL_MARKERS):
            return True
    return False


def permission_retry_delay_s(streak: int) -> int:
    """Back off repeated model/tooling mistakes without waiting a full goal hour."""
    exponent = max(0, min(int(streak) - 1, 4))
    return min(_PERMISSION_RETRY_MAX_S, _PERMISSION_RETRY_BASE_S * (2 ** exponent))


async def _run_goal_with_permission_recovery(self: Any) -> None:
    await _ORIGINAL_RUN_GOAL(self)

    denied = output_has_permission_denial(tuple(self.output_tail))
    if denied and self.last_status != "quota_wait":
        streak = int(getattr(self, "_permission_denial_streak_v422", 0) or 0) + 1
        self._permission_denial_streak_v422 = streak
        delay = permission_retry_delay_s(streak)
        self.next_run_at = time.time() + delay
        self.last_status = "permission_retry"
        self._save()
        logger.warning(
            "AGY goal ended after a denied headless tool action; retrying with strict permissions in %ss streak=%d",
            delay,
            streak,
        )
        return

    self._permission_denial_streak_v422 = 0


def install_permission_guard_v422() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    base.GoalSupervisor._run_goal = _run_goal_with_permission_recovery
    _INSTALLED = True
    logger.info("AGY v4.2.2 permission-denial recovery enabled")
