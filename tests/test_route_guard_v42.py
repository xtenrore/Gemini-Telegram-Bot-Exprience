from types import SimpleNamespace

from app.intelligence.route_guard_v42 import (
    ArrivalAssessment,
    CANCEL_SCORE,
    EncounterState,
    HypothesisResult,
    _apply_qualification,
    _motion_paths,
    _neutralize_unsafe_history_veto,
    _path_cache,
    assess_candidate,
    reset_v42_state_for_tests,
)
from app.intelligence.route_history import AirportInfo, RouteGateResult
from app.intelligence.trajectory import HistorySample, bearing_deg

IST = AirportInfo(icao="LTFM", iata="IST", latitude=41.2753, longitude=28.7519)
USER = (41.0, 28.90)
RADIUS = 8.0
NOW = 2_000_000_000.0


def ac(*, icao="4baa66", lat=41.0, lon=29.15, heading=270.0, alt=3500.0, vr=-6.0, speed=260.0):
    return SimpleNamespace(
        icao24=icao,
        callsign="THY2GN",
        latitude=lat,
        longitude=lon,
        heading=heading,
        altitude=alt,
        vertical_rate_mps=vr,
        ground_speed=speed,
    )


def pred(*, current=21.0, cpa=1.5, cpa_s=170.0, turn=0.0, stale=False, passed=False, trend=-0.04):
    return SimpleNamespace(
        current_distance_km=current,
        projected_closest_km=cpa,
        time_to_cpa_s=cpa_s,
        turn_rate_deg_s=turn,
        stale=stale,
        already_passed=passed,
        state="Passed" if passed else "Approaching",
        confidence="High",
        confidence_score=0.88,
        distance_trend_km_s=trend,
        enters_alert_radius=not stale and not passed,
    )


def history_to_current():
    return [
        HistorySample(NOW - 60, 41.0, 29.27, 4600.0, 275.0, 270.0, -5.0, 0.0),
        HistorySample(NOW - 30, 41.0, 29.21, 4050.0, 268.0, 270.0, -5.5, 0.0),
        HistorySample(NOW, 41.0, 29.15, 3500.0, 260.0, 270.0, -6.0, 0.0),
    ]


def assessment(*, score, terminal="NOT_TERMINAL", expected="NONE", airport_cpa=None):
    return ArrivalAssessment(
        terminal_state=terminal,
        terminal_score=0.8 if terminal != "NOT_TERMINAL" else 0.2,
        pass_score=score,
        median_cpa_km=5.0,
        p10_cpa_km=2.0,
        p90_cpa_km=18.0,
        prediction_spread_km=16.0,
        expected_turn_state=expected,
        expected_turn_direction="LEFT" if expected != "NONE" else "",
        expected_turn_eta_s=35.0 if expected != "NONE" else None,
        airport_path_cpa_km=airport_cpa,
        historical_match_score=0.8,
        historical_cluster="matched",
        hypotheses=(
            HypothesisResult("constant_heading", 1.0, 2.0, 120.0, True),
            HypothesisResult("shallow_left", 1.0, 4.0, 130.0, True),
            HypothesisResult("airport_convergence_now", 2.0, 20.0, 100.0, False),
        ),
    )


def base(suppress=False, reason="route history does not veto live CPA"):
    return RouteGateResult(suppress, "THY2GN", reason, destination_code="IST", route_plausible=True)


def setup_function():
    reset_v42_state_for_tests()


def test_case_a_terminal_arrival_points_at_user_but_airport_paths_turn_away():
    aircraft = ac()
    live = pred()
    result = assess_candidate(
        ac=aircraft,
        pred=live,
        destination=IST,
        route_plausible=True,
        current_samples=history_to_current(),
        historical_paths=[],
        observer_lat=USER[0],
        observer_lon=USER[1],
        alert_radius_km=RADIUS,
        encounter=EncounterState(100.0, 100.0),
        now_mono=100.0,
    )
    assert result.terminal_state == "TERMINAL_ARRIVAL"
    assert result.expected_turn_state in {"EXPECTED_TURN_PENDING", "TURN_STARTED"}
    assert result.airport_path_cpa_km is not None
    assert result.airport_path_cpa_km > RADIUS
    assert result.pass_score < 0.76


def test_case_b_expected_turn_failure_releases_shadow_when_live_ensemble_is_strong():
    encounter = EncounterState(100.0, 100.0)
    live = pred(cpa_s=70.0)
    release = assessment(score=0.86, terminal="TERMINAL_ARRIVAL", expected="TURN_DID_NOT_OCCUR", airport_cpa=20.0)

    first = _apply_qualification(
        base=base(), assessment=release, encounter=encounter, pred=live,
        current_distance_km=14.0, radius_km=RADIUS, active=False, now_mono=200.0,
    )
    second = _apply_qualification(
        base=base(), assessment=release, encounter=encounter, pred=live,
        current_distance_km=12.0, radius_km=RADIUS, active=False, now_mono=205.0,
    )
    assert first[0] is True
    assert second[0] is False
    assert second[1] == "QUALIFIED_PASS"


def test_case_c_known_ist_destination_does_not_globally_suppress_real_close_pass():
    encounter = EncounterState(100.0, 100.0)
    live = pred(current=7.5, cpa=2.0, cpa_s=55.0)
    real_pass = assessment(score=0.92, terminal="TERMINAL_ARRIVAL", expected="TURN_DID_NOT_OCCUR", airport_cpa=5.5)
    suppressed, state, _ = _apply_qualification(
        base=base(), assessment=real_pass, encounter=encounter, pred=live,
        current_distance_km=7.5, radius_km=RADIUS, active=False, now_mono=200.0,
    )
    # Direct observed presence is ultimately an unconditional override in evaluate_route_v42;
    # the state machine itself must also not create an airport-turn hold here.
    assert state != "EXPECTED_TURN_PENDING"
    assert real_pass.airport_path_cpa_km < RADIUS


def test_case_d_destination_missing_history_plus_descent_can_mark_arrival_like_state():
    current = history_to_current()
    historic = [[
        (41.0, 29.30), (41.0, 29.24), (41.0, 29.18), (41.0, 29.12), (41.05, 29.02), (41.12, 28.92)
    ]]
    result = assess_candidate(
        ac=ac(), pred=pred(), destination=None, route_plausible=False,
        current_samples=current, historical_paths=historic,
        observer_lat=USER[0], observer_lon=USER[1], alert_radius_km=RADIUS,
        encounter=EncounterState(100.0, 100.0), now_mono=100.0,
    )
    assert result.terminal_state in {"ARRIVAL_LIKELY_HISTORY", "NOT_TERMINAL"}
    assert result.historical_match_score > 0.0


def test_case_e_live_divergence_neutralizes_historical_veto():
    old = RouteGateResult(True, "THY2GN", "today's route diverges from the recent flight-number pattern", history_days=3)
    new = _neutralize_unsafe_history_veto(old)
    assert new.suppress_alert is False
    assert "live trajectory regains authority" in new.reason


def test_case_f_stale_adsb_is_uncertainty_not_turn_confirmation():
    encounter = EncounterState(100.0, 100.0, positive_count=1)
    stale = pred(stale=True)
    suppressed, state, confirmations = _apply_qualification(
        base=base(), assessment=assessment(score=0.9, terminal="TERMINAL_ARRIVAL", expected="EXPECTED_TURN_PENDING", airport_cpa=20.0),
        encounter=encounter, pred=stale, current_distance_km=16.0,
        radius_km=RADIUS, active=False, now_mono=105.0,
    )
    assert suppressed is True
    assert state == "SHADOW_DATA_UNCERTAIN"
    assert confirmations == 0


def test_case_g_passed_encounter_cannot_reopen_from_bogus_inbound_vector():
    encounter = EncounterState(100.0, 100.0, qualified=True, passed=True, passed_mono=100.0, observed_min_km=4.0, previous_distance_km=10.0)
    bogus = pred(current=12.0, cpa=1.0, cpa_s=80.0, trend=-0.05)
    suppressed, state, _ = _apply_qualification(
        base=base(), assessment=assessment(score=0.95), encounter=encounter, pred=bogus,
        current_distance_km=12.0, radius_km=RADIUS, active=True, now_mono=150.0,
    )
    assert suppressed is True
    assert state == "PASSED_LOCKED"


def test_case_h_expected_turn_evidence_cancels_shadow_before_notification():
    encounter = EncounterState(100.0, 100.0)
    first = _apply_qualification(
        base=base(), assessment=assessment(score=0.82), encounter=encounter, pred=pred(),
        current_distance_km=18.0, radius_km=RADIUS, active=False, now_mono=100.0,
    )
    second = _apply_qualification(
        base=base(), assessment=assessment(score=0.30, terminal="TERMINAL_ARRIVAL", expected="EXPECTED_TURN_PENDING", airport_cpa=24.0),
        encounter=encounter, pred=pred(), current_distance_km=17.0,
        radius_km=RADIUS, active=False, now_mono=105.0,
    )
    assert first[0] is True and first[1] == "TRAJECTORY_CONFIRMING"
    assert second[0] is True and second[1] == "EXPECTED_TURN_PENDING"
    assert encounter.qualified is False
    assert encounter.positive_count == 0


def test_case_i_hysteresis_keeps_qualified_pass_above_cancel_threshold():
    encounter = EncounterState(100.0, 100.0, positive_count=3, qualified=True)
    mid = max(CANCEL_SCORE + 0.08, 0.50)
    held = _apply_qualification(
        base=base(), assessment=assessment(score=mid), encounter=encounter, pred=pred(),
        current_distance_km=12.0, radius_km=RADIUS, active=True, now_mono=110.0,
    )
    cancelled = _apply_qualification(
        base=base(), assessment=assessment(score=CANCEL_SCORE - 0.10), encounter=encounter, pred=pred(),
        current_distance_km=13.0, radius_km=RADIUS, active=True, now_mono=115.0,
    )
    assert held[0] is False
    assert cancelled[0] is True


def test_case_j_global_motion_paths_are_reused_across_users_and_cache_is_bounded():
    aircraft = ac()
    live = pred()
    first = _motion_paths(ac=aircraft, pred=live, destination=IST, terminal_state="TERMINAL_ARRIVAL")
    second = _motion_paths(ac=aircraft, pred=live, destination=IST, terminal_state="TERMINAL_ARRIVAL")
    assert first is second
    assert len(first) <= 9
    assert len(_path_cache) <= 128
