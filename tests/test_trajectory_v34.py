from app.intelligence.trajectory import HistorySample, predict_trajectory

NOW = 2_000_000_000.0
USER = (41.0, 29.0)


def sample(lat, lon, heading, speed=420, t=NOW, age=0, alt=3500):
    return HistorySample(t, lat, lon, alt, speed, heading, 0.0, age)


def test_direct_toward_user_enters_radius():
    hist = [sample(41.20, 29.0, 180, t=NOW - 10), sample(41.18, 29.0, 180, t=NOW)]
    p = predict_trajectory(hist, *USER, 8, now=NOW)
    assert p.enters_alert_radius
    assert p.state in {"Approaching", "Passing nearby"}
    assert p.projected_closest_km < 2
    assert p.time_to_cpa_s and p.time_to_cpa_s > 0


def test_moving_away_does_not_alert():
    hist = [sample(41.08, 29.0, 0, t=NOW - 10), sample(41.10, 29.0, 0, t=NOW)]
    p = predict_trajectory(hist, *USER, 8, now=NOW)
    assert not p.enters_alert_radius
    assert p.state in {"Moving away", "Passed"}


def test_crossing_far_does_not_alert_even_if_destination_ist():
    # Destination IST is deliberately irrelevant and is not an input to the predictor.
    hist = [sample(41.30, 28.80, 90, t=NOW - 10), sample(41.30, 28.83, 90, t=NOW)]
    p = predict_trajectory(hist, *USER, 15, now=NOW)
    assert not p.enters_alert_radius
    assert p.projected_closest_km > 25
    assert p.radius_entry_s is None


def test_crossing_near_alerts():
    hist = [sample(41.04, 28.80, 90, t=NOW - 10), sample(41.04, 28.84, 90, t=NOW)]
    p = predict_trajectory(hist, *USER, 8, now=NOW)
    assert p.enters_alert_radius
    assert p.projected_closest_km < 6


def test_stale_data_never_precise_alert():
    p = predict_trajectory([sample(41.1, 29.0, 180, t=NOW - 60, age=60)], *USER, 15, now=NOW)
    assert p.stale
    assert not p.enters_alert_radius
    assert p.time_to_cpa_s is None
    assert p.confidence == "Uncertain"


def test_turning_trajectory_reduces_confidence():
    straight = [sample(41.2, 29.0, 180, t=NOW - 10), sample(41.18, 29.0, 180, t=NOW - 5), sample(41.16, 29.0, 180, t=NOW)]
    turning = [sample(41.2, 29.0, 150, t=NOW - 10), sample(41.18, 29.0, 180, t=NOW - 5), sample(41.16, 29.0, 215, t=NOW)]
    ps = predict_trajectory(straight, *USER, 8, now=NOW)
    pt = predict_trajectory(turning, *USER, 8, now=NOW)
    assert abs(pt.turn_rate_deg_s) > abs(ps.turn_rate_deg_s)
    assert pt.confidence_score < ps.confidence_score
