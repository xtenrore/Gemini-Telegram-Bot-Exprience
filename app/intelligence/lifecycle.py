"""Pure alert lifecycle decisions for Plane? v3.4."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LifecycleDecision:
    stage: str
    should_have_live_message: bool
    reason: str


def decide_lifecycle(prediction, best_start_s: float | None = None, best_end_s: float | None = None) -> LifecycleDecision:
    """Map a trajectory prediction to the spotting alert lifecycle.

    Candidate remains silent. Prepare creates the first live message; later states
    edit that same message. Passed is reserved for a previously-qualified pass.
    """
    if getattr(prediction, "stale", False):
        return LifecycleDecision("uncertain", False, "ADS-B position is stale")
    if getattr(prediction, "already_passed", False) or getattr(prediction, "state", "") == "Passed":
        return LifecycleDecision("passed", True, "closest approach has occurred")
    if not getattr(prediction, "enters_alert_radius", False):
        return LifecycleDecision("detection", False, "projected CPA remains outside alert radius")

    cpa = getattr(prediction, "time_to_cpa_s", None)
    start = best_start_s if best_start_s is not None else (max(0.0, cpa - 25.0) if cpa is not None else None)
    end = best_end_s if best_end_s is not None else ((cpa + 10.0) if cpa is not None else None)
    if start is None:
        return LifecycleDecision("candidate", False, "trajectory qualifies but shooting window is uncertain")
    if start <= 0 <= (end if end is not None else 0):
        return LifecycleDecision("photo_now", True, "best shooting window is active")
    if start <= 120:
        return LifecycleDecision("camera_ready", True, "shooting window is within two minutes")
    if start <= 300:
        return LifecycleDecision("prepare", True, "shooting window is within five minutes")
    return LifecycleDecision("candidate", False, "trajectory qualifies but preparation is not yet useful")


def prediction_changed(previous_cpa_km: float | None, new_cpa_km: float, alert_radius_km: float) -> bool:
    if previous_cpa_km is None:
        return False
    delta = abs(float(new_cpa_km) - float(previous_cpa_km))
    return delta >= max(1.5, alert_radius_km * 0.20)
