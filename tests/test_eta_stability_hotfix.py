from types import SimpleNamespace

from app.intelligence.lifecycle import advance_cancellation_confirmation, should_cancel_active_alert
from app.intelligence.trajectory import HistorySample, TrajectoryHistoryStore, predict_trajectory

NOW = 2_000_000_000.0
USER = (41.0, 29.0)


def sample(lat, lon, heading, speed=420, t=NOW, age=0, alt=3500):
    return HistorySample(t, lat, lon, alt, speed, heading, 0.0, age)


def test_history_rejects_impossible_provider_teleport():
    store = TrajectoryHistoryStore()
    store.add("abc123", sample(41.20, 29.00, 180, t=NOW - 5))
    before = store.get("abc123")[-1]
    store.add("abc123", sample(41.20, 29.30, 90, t=NOW))
    after = store.get("abc123")[-1]
    assert after.timestamp == before.timestamp
    assert (after.latitude, after.longitude) == (before.latitude, before.longitude)


def test_single_heading_spike_does_not_destroy_good_cpa():
    hist = [
        sample(41.24, 29.0, 180, t=NOW - 15),
        sample(41.21, 29.0, 180, t=NOW - 10),
        sample(41.18, 29.0, 180, t=NOW - 5),
        sample(41.15, 29.0, 85, t=NOW),
    ]
    p = predict_trajectory(hist, *USER, 8, now=NOW)
    assert p.enters_alert_radius
    assert p.projected_closest_km < 3


def test_uncertain_single_bad_prediction_is_not_cancellation_evidence():
    pred = SimpleNamespace(
        stale=False,
        already_passed=False,
        state="Will not approach",
        enters_alert_radius=False,
        projected_closest_km=28.0,
        distance_trend_km_s=None,
        turning_away=False,
        confidence_score=0.2,
    )
    assert should_cancel_active_alert(pred, 5.0, 15.0) is False


def test_confident_clear_miss_can_invalidate_eta_before_distance_turns_outward():
    pred = SimpleNamespace(
        stale=False,
        already_passed=False,
        state="Will not approach",
        enters_alert_radius=False,
        projected_closest_km=19.0,
        distance_trend_km_s=-0.03,
        turning_away=False,
        confidence_score=0.72,
    )
    assert should_cancel_active_alert(pred, 5.8, 8.0) is True


def test_low_confidence_extreme_miss_can_retire_obsolete_eta_after_confirmation():
    pred = SimpleNamespace(
        stale=False,
        already_passed=False,
        state="Will not approach",
        enters_alert_radius=False,
        projected_closest_km=47.0,
        distance_trend_km_s=-0.01,
        turning_away=False,
        confidence_score=0.42,
    )
    candidate = should_cancel_active_alert(pred, 6.9, 8.0)
    assert candidate is True
    confirmed, count = advance_cancellation_confirmation(0, candidate)
    assert not confirmed and count == 1
    confirmed, count = advance_cancellation_confirmation(count, candidate)
    assert not confirmed and count == 2
    confirmed, count = advance_cancellation_confirmation(count, candidate)
    assert confirmed and count == 3


def test_low_confidence_non_extreme_miss_still_holds_eta():
    pred = SimpleNamespace(
        stale=False,
        already_passed=False,
        state="Will not approach",
        enters_alert_radius=False,
        projected_closest_km=20.0,
        distance_trend_km_s=-0.01,
        turning_away=False,
        confidence_score=0.42,
    )
    assert should_cancel_active_alert(pred, 6.9, 8.0) is False


def test_small_miss_without_outward_motion_does_not_cancel_eta():
    pred = SimpleNamespace(
        stale=False,
        already_passed=False,
        state="Will not approach",
        enters_alert_radius=False,
        projected_closest_km=11.0,
        distance_trend_km_s=-0.03,
        turning_away=False,
        confidence_score=0.8,
    )
    assert should_cancel_active_alert(pred, 5.8, 8.0) is False


def test_cancellation_needs_three_consecutive_credible_cycles():
    pred = SimpleNamespace(
        stale=False,
        already_passed=False,
        state="Moving away",
        enters_alert_radius=False,
        projected_closest_km=25.0,
        distance_trend_km_s=0.02,
        turning_away=False,
        confidence_score=0.7,
    )
    candidate = should_cancel_active_alert(pred, 5.0, 15.0)
    assert candidate
    confirmed, count = advance_cancellation_confirmation(0, candidate)
    assert not confirmed and count == 1
    confirmed, count = advance_cancellation_confirmation(count, candidate)
    assert not confirmed and count == 2
    confirmed, count = advance_cancellation_confirmation(count, candidate)
    assert confirmed and count == 3
    confirmed, count = advance_cancellation_confirmation(count, False)
    assert not confirmed and count == 0
