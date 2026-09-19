from pathlib import Path
from types import SimpleNamespace

import pytest

from app.intelligence.lifecycle import should_cancel_active_alert
from app.intelligence.requalification_guard_v43 import (
    CancellationLatch,
    _apply_latch,
)
from app.intelligence.route_guard_v42 import RouteGateResultV42
from app.intelligence.trajectory import HistorySample
from app.intelligence.trajectory_hotfix_v43 import (
    _midpoint_motion_step,
    predict_trajectory_v43,
)
from app.next_hour_shadow_v43 import _history_quality_reason


def test_low_confidence_turn_away_can_accumulate_cancellation_evidence():
    prediction = SimpleNamespace(
        stale=False,
        already_passed=False,
        state="Turning away",
        enters_alert_radius=False,
        projected_closest_km=20.0,
        distance_trend_km_s=0.006,
        turning_away=True,
        confidence_score=0.20,
    )
    assert should_cancel_active_alert(prediction, 5.0, 9.0)


def test_cancelled_encounter_cannot_predictively_requalify_until_inside_radius():
    state = CancellationLatch(last_seen_mono=0.0)
    outside = SimpleNamespace(stale=False, current_distance_km=12.0)
    suppressed = RouteGateResultV42(
        suppress_alert=True,
        callsign="THY1017",
        reason="v4.2 ACTIVE_BELOW_CANCEL_HYSTERESIS",
        qualification_state="ACTIVE_BELOW_CANCEL_HYSTERESIS",
    )

    for _ in range(2):
        result = _apply_latch(
            suppressed,
            pred=outside,
            encounter_was_qualified=True,
            state=state,
            radius_km=9.0,
        )
        assert result.suppress_alert
        assert not state.latched

    result = _apply_latch(
        suppressed,
        pred=outside,
        encounter_was_qualified=True,
        state=state,
        radius_km=9.0,
    )
    assert state.latched
    assert result.qualification_state == "CANCEL_LATCHED"

    recovered_prediction = RouteGateResultV42(
        suppress_alert=False,
        callsign="THY1017",
        reason="v4.2 QUALIFIED_PASS",
        qualification_state="QUALIFIED_PASS",
    )
    result = _apply_latch(
        recovered_prediction,
        pred=outside,
        encounter_was_qualified=True,
        state=state,
        radius_km=9.0,
    )
    assert result.suppress_alert
    assert result.qualification_state == "CANCEL_LATCHED"

    inside = SimpleNamespace(stale=False, current_distance_km=8.8)
    result = _apply_latch(
        recovered_prediction,
        pred=inside,
        encounter_was_qualified=True,
        state=state,
        radius_km=9.0,
    )
    assert not result.suppress_alert
    assert not state.latched
    assert state.suppress_count == 0


def test_midpoint_step_uses_average_speed_and_heading():
    next_speed, next_heading, midpoint_speed, midpoint_heading = _midpoint_motion_step(
        100.0,
        359.0,
        acceleration_kts_s=1.0,
        turn_rate_deg_s=1.0,
        acceleration_factor=1.0,
        turn_factor=1.0,
        step_s=3.0,
    )
    assert next_speed == pytest.approx(103.0)
    assert midpoint_speed == pytest.approx(101.5)
    assert next_heading == pytest.approx(2.0)
    assert midpoint_heading == pytest.approx(0.5)


def test_midpoint_predictor_keeps_fresh_in_radius_presence_authoritative():
    samples = [
        HistorySample(
            timestamp=100.0,
            latitude=0.035,
            longitude=0.0,
            altitude_m=3000.0,
            speed_kts=260.0,
            heading_deg=0.0,
        ),
        HistorySample(
            timestamp=110.0,
            latitude=0.045,
            longitude=0.0,
            altitude_m=3000.0,
            speed_kts=260.0,
            heading_deg=0.0,
        ),
    ]
    prediction = predict_trajectory_v43(
        samples,
        0.0,
        0.0,
        9.0,
        now=110.0,
    )
    assert prediction.current_distance_km < 9.0
    assert prediction.enters_alert_radius
    assert not prediction.already_passed
    assert prediction.state == "Passing nearby"
    assert prediction.radius_entry_s == 0.0


def test_next_hour_shadow_rejects_single_day_and_ambiguous_history():
    assert _history_quality_reason(1, 0.0) == "insufficient_history_days"
    assert _history_quality_reason(2, 1900.0) == "ambiguous_or_multimodal_time_history"
    assert _history_quality_reason(2, 900.0) == ""


def test_agy_headless_permissions_cover_observed_safe_audit_commands():
    entrypoint = Path("scripts/agy-worker-entrypoint.sh").read_text()
    for rule in ("command(python3)", "command(grep)", "command(jq)", "command(ls)"):
        assert rule in entrypoint
    assert "--dangerously-skip-permissions" not in entrypoint
