"""Plane Alerts v4.4 transient-turn protection.

A straight-line CPA is a useful fast detector, but it is not a future intent
model while an aircraft is actively turning. This guard compares the straight
CPA with two independent curved continuations derived from fresh ADS-B motion.
It can only suppress an otherwise-qualifying prediction; it can never create an
alert. Fresh physical entry into the radius always wins.
"""
from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, replace
from statistics import median
from typing import Any, Iterable

from app.decision_recorder import record_decision, trajectory_samples
from app.intelligence import requalification_guard_v43 as v43
from app.intelligence import route_guard_v2 as v2
from app.intelligence import route_guard_v42 as v42
from app.intelligence import route_history as route_mod

logger = logging.getLogger(__name__)

_MIN_TURN_RATE_DEG_S = 0.12
_MIN_NET_TURN_DEG = 3.0
_MIN_TURN_SPAN_S = 5.0
_MAX_SAMPLE_AGE_S = 25.0
_MIN_DIRECTION_CONSISTENCY = 0.67
_MIN_CURVED_MARGIN_KM = 2.0
_INSTALLED = False
_BASE_EVALUATE = None


@dataclass(slots=True, frozen=True)
class TurnEvidence:
    active: bool
    rate_deg_s: float
    net_turn_deg: float
    span_s: float
    direction_consistency: float
    curved_cpa_km: float | None
    observed_turn_cpa_km: float | None
    curvature_cpa_km: float | None
    straight_cpa_km: float
    reason_code: str
    explanation: str


def _angle_delta(target: float, current: float) -> float:
    return (target - current + 180.0) % 360.0 - 180.0


def _fresh_turn_shape(samples: Iterable[Any], now: float) -> tuple[float, float, float, float]:
    usable = []
    for sample in sorted(list(samples), key=lambda item: float(getattr(item, "timestamp", 0.0) or 0.0)):
        heading = getattr(sample, "heading_deg", None)
        timestamp = float(getattr(sample, "timestamp", 0.0) or 0.0)
        age = max(
            float(getattr(sample, "position_age_s", 0.0) or 0.0),
            max(0.0, now - timestamp),
        )
        if heading is None or timestamp <= 0 or age > _MAX_SAMPLE_AGE_S:
            continue
        usable.append((timestamp, float(heading) % 360.0))
    usable = usable[-8:]
    if len(usable) < 3:
        return 0.0, 0.0, 0.0, 0.0

    deltas: list[float] = []
    rates: list[float] = []
    for (t0, h0), (t1, h1) in zip(usable, usable[1:]):
        dt = t1 - t0
        if dt < 0.75 or dt > 20.0:
            continue
        delta = _angle_delta(h1, h0)
        if abs(delta) > 45.0:
            continue
        deltas.append(delta)
        rates.append(delta / dt)
    if len(rates) < 2:
        return 0.0, 0.0, 0.0, 0.0

    rate = median(rates)
    direction = 1.0 if rate >= 0 else -1.0
    consistent = sum(1 for value in rates if value * direction > 0.02) / len(rates)
    span = usable[-1][0] - usable[0][0]
    net = abs(sum(deltas))
    return rate, net, span, consistent


def _curved_cpas(
    ac: Any,
    pred: Any,
    result: route_mod.RouteGateResult,
    *,
    user_lat: float,
    user_lon: float,
    radius_km: float,
    self_service: Any,
) -> tuple[float | None, float | None, float | None]:
    key = route_mod.normalize_flight_key(getattr(ac, "callsign", ""))
    route, _ = v2._cached_route(self_service, key) if key else (None, False)
    destination = route.destination if route else None
    terminal_state = str(getattr(result, "terminal_arrival_state", "NOT_TERMINAL") or "NOT_TERMINAL")
    paths = v42._motion_paths(ac=ac, pred=pred, destination=destination, terminal_state=terminal_state)
    evaluated = {
        path.name: v42._evaluate_motion_path(
            path,
            observer_lat=user_lat,
            observer_lon=user_lon,
            radius_km=radius_km,
        )
        for path in paths
        if path.name in {"observed_turn", "curvature"}
    }
    observed = evaluated.get("observed_turn")
    curvature = evaluated.get("curvature")
    values = [item.cpa_km for item in (observed, curvature) if item is not None]
    curved = min(values) if values else None
    return curved, observed.cpa_km if observed else None, curvature.cpa_km if curvature else None


def assess_transient_alignment(
    *,
    ac: Any,
    pred: Any,
    result: route_mod.RouteGateResult,
    current_samples: Iterable[Any],
    user_lat: float,
    user_lon: float,
    alert_radius_km: float,
    self_service: Any,
    now: float | None = None,
) -> TurnEvidence:
    effective_now = time.time() if now is None else float(now)
    current_distance = float(getattr(pred, "current_distance_km", math.inf) or math.inf)
    straight_cpa = float(getattr(pred, "projected_closest_km", math.inf) or math.inf)
    if bool(getattr(pred, "stale", False)) or current_distance <= float(alert_radius_km):
        return TurnEvidence(False, 0.0, 0.0, 0.0, 0.0, None, None, None, straight_cpa, "", "")

    hist_rate, net_turn, span, consistency = _fresh_turn_shape(current_samples, effective_now)
    predictor_rate = float(getattr(pred, "turn_rate_deg_s", 0.0) or 0.0)
    rate = hist_rate if abs(hist_rate) >= 0.05 else predictor_rate
    active_turn = (
        abs(rate) >= _MIN_TURN_RATE_DEG_S
        and net_turn >= _MIN_NET_TURN_DEG
        and span >= _MIN_TURN_SPAN_S
        and consistency >= _MIN_DIRECTION_CONSISTENCY
    )
    if not active_turn or straight_cpa > float(alert_radius_km):
        return TurnEvidence(False, rate, net_turn, span, consistency, None, None, None, straight_cpa, "", "")

    curved, observed, curvature = _curved_cpas(
        ac,
        pred,
        result,
        user_lat=user_lat,
        user_lon=user_lon,
        radius_km=alert_radius_km,
        self_service=self_service,
    )
    if curved is None:
        return TurnEvidence(False, rate, net_turn, span, consistency, None, observed, curvature, straight_cpa, "", "")

    margin = max(_MIN_CURVED_MARGIN_KM, float(alert_radius_km) * 0.15)
    curved_disagrees = curved > float(alert_radius_km) + margin
    if not curved_disagrees:
        # A real continuing turn toward the observer remains eligible immediately.
        return TurnEvidence(False, rate, net_turn, span, consistency, curved, observed, curvature, straight_cpa, "", "")

    terminal_state = str(getattr(result, "terminal_arrival_state", "NOT_TERMINAL") or "NOT_TERMINAL")
    destination = str(getattr(result, "destination_code", "") or "").upper()
    terminal_support = terminal_state != "NOT_TERMINAL" or destination in {"IST", "LTFM"}
    reason_code = "terminal_arrival_transient_alignment" if terminal_support else "active_turn_transient_alignment"
    explanation = (
        "aircraft briefly aligns with observer while a fresh continuing-turn path remains outside the alert radius"
    )
    return TurnEvidence(
        True,
        rate,
        net_turn,
        span,
        consistency,
        curved,
        observed,
        curvature,
        straight_cpa,
        reason_code,
        explanation,
    )


def _with_suppression(result: route_mod.RouteGateResult, evidence: TurnEvidence) -> route_mod.RouteGateResult:
    reason = (
        f"{evidence.reason_code}: {evidence.explanation}; "
        f"straight_cpa={evidence.straight_cpa_km:.2f}km curved_cpa={float(evidence.curved_cpa_km):.2f}km "
        f"turn_rate={evidence.rate_deg_s:.3f}deg/s net_turn={evidence.net_turn_deg:.1f}deg"
    )
    kwargs = {"suppress_alert": True, "reason": reason}
    if hasattr(result, "qualification_state"):
        kwargs["qualification_state"] = evidence.reason_code.upper()
    return replace(result, **kwargs)


async def evaluate_route_v44(
    self: route_mod.RouteHistoryService,
    ac: Any,
    pred: Any,
    *,
    user_lat: float,
    user_lon: float,
    alert_radius_km: float,
    current_samples: Iterable[Any],
) -> route_mod.RouteGateResult:
    assert _BASE_EVALUATE is not None
    samples = list(current_samples)
    result = await _BASE_EVALUATE(
        self,
        ac,
        pred,
        user_lat=user_lat,
        user_lon=user_lon,
        alert_radius_km=alert_radius_km,
        current_samples=samples,
    )
    evidence = assess_transient_alignment(
        ac=ac,
        pred=pred,
        result=result,
        current_samples=samples,
        user_lat=user_lat,
        user_lon=user_lon,
        alert_radius_km=alert_radius_km,
        self_service=self,
    )
    if evidence.active:
        result = _with_suppression(result, evidence)
        logger.info(
            "transient_turn_suppressed code=%s flight=%s icao=%s destination=%s straight_cpa=%.2f curved_cpa=%.2f turn_rate=%.3f",
            evidence.reason_code,
            getattr(ac, "callsign", ""),
            getattr(ac, "icao24", ""),
            getattr(result, "destination_code", ""),
            evidence.straight_cpa_km,
            float(evidence.curved_cpa_km or math.inf),
            evidence.rate_deg_s,
        )

    reason_code = evidence.reason_code or str(getattr(result, "qualification_state", "route_decision") or "route_decision").lower()
    state = str(getattr(result, "qualification_state", "suppress" if result.suppress_alert else "qualify") or "")
    record_decision(
        subsystem="prediction",
        event="route_gate",
        state_key=f"route:{getattr(ac, 'icao24', '')}:{round(float(user_lat), 4)}:{round(float(user_lon), 4)}:{round(float(alert_radius_km), 1)}",
        state=state,
        decision="suppress" if result.suppress_alert else "allow",
        reason_code=reason_code,
        dedup_signature={
            "state": state,
            "suppress": bool(result.suppress_alert),
            "reason_code": reason_code,
            "straight_bucket": round(float(getattr(pred, "projected_closest_km", math.inf)), 1),
            "curved_bucket": round(float(evidence.curved_cpa_km), 1) if evidence.curved_cpa_km is not None else None,
            "destination": getattr(result, "destination_code", ""),
        },
        evidence={
            "flight_number": getattr(ac, "callsign", ""),
            "icao24": getattr(ac, "icao24", ""),
            "aircraft_type": getattr(ac, "aircraft_type", ""),
            "position": {"latitude": getattr(ac, "latitude", None), "longitude": getattr(ac, "longitude", None)},
            "altitude_m": getattr(ac, "altitude", None),
            "groundspeed_kts": getattr(ac, "ground_speed", None),
            "track_deg": getattr(ac, "heading", None),
            "vertical_rate_mps": getattr(ac, "vertical_rate_mps", None),
            "position_age_s": getattr(ac, "position_age_s", None),
            "recent_trajectory": trajectory_samples(samples),
            "turn_rate_deg_s": evidence.rate_deg_s or getattr(pred, "turn_rate_deg_s", None),
            "turn_span_s": evidence.span_s,
            "turn_net_deg": evidence.net_turn_deg,
            "turn_direction_consistency": evidence.direction_consistency,
            "user_distance_km": getattr(pred, "current_distance_km", None),
            "straight_cpa_km": getattr(pred, "projected_closest_km", None),
            "curved_cpa_km": evidence.curved_cpa_km,
            "observed_turn_cpa_km": evidence.observed_turn_cpa_km,
            "curvature_cpa_km": evidence.curvature_cpa_km,
            "time_to_cpa_s": getattr(pred, "time_to_cpa_s", None),
            "destination": getattr(result, "destination_code", ""),
            "terminal_arrival_state": getattr(result, "terminal_arrival_state", ""),
            "terminal_arrival_score": getattr(result, "terminal_arrival_score", None),
            "route_history_days": getattr(result, "history_days", None),
            "route_similar_days": getattr(result, "similar_days", None),
            "route_similarity_km": getattr(result, "similarity_km", None),
            "historical_match_score": getattr(result, "historical_match_score", None),
            "prediction_confidence": getattr(pred, "confidence", None),
            "prediction_confidence_score": getattr(pred, "confidence_score", None),
            "ensemble_pass_score": getattr(result, "ensemble_pass_score", None),
            "live_pass_score": getattr(result, "live_pass_score", None),
            "route_reason": getattr(result, "reason", ""),
            "eta_s": getattr(pred, "time_to_cpa_s", None),
            "alert_delivered": False,
        },
    )
    return result


def install_transient_turn_guard_v44() -> None:
    global _INSTALLED, _BASE_EVALUATE
    if _INSTALLED:
        return
    _BASE_EVALUATE = route_mod.RouteHistoryService.evaluate
    route_mod.RouteHistoryService.evaluate = evaluate_route_v44
    _INSTALLED = True
    logger.info(
        "Plane Alerts v4.4 transient-turn guard enabled: rate>=%.2fdeg/s net>=%.1fdeg span>=%.1fs",
        _MIN_TURN_RATE_DEG_S,
        _MIN_NET_TURN_DEG,
        _MIN_TURN_SPAN_S,
    )


def reset_v44_transient_state_for_tests() -> None:
    # Guard state is intentionally derived only from bounded ADS-B samples; no
    # independent hysteresis state exists to accidentally create a time delay.
    return None
