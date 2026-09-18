from dataclasses import dataclass

from app.worker.critical_timing import (
    _LIVE_ALERT_MAX_POSITION_AGE_S,
    _harden_prediction_freshness,
)


@dataclass(frozen=True)
class Prediction:
    state: str = "Approaching"
    confidence: str = "High"
    confidence_score: float = 0.9
    enters_alert_radius: bool = True
    stale: bool = False
    reason: str = "live CPA"


@dataclass(frozen=True)
class Sample:
    timestamp: float
    position_age_s: float


def test_old_continuity_position_cannot_create_late_alert():
    now = 1000.0
    prediction = Prediction()
    hardened = _harden_prediction_freshness(
        prediction,
        [Sample(timestamp=986.2, position_age_s=13.8)],
        now=now,
    )

    assert _LIVE_ALERT_MAX_POSITION_AGE_S == 12.0
    assert hardened.stale is True
    assert hardened.enters_alert_radius is False
    assert hardened.state == "Prediction uncertain"
    assert "13.8s" in hardened.reason


def test_fresh_position_keeps_live_prediction_unchanged():
    prediction = Prediction()
    hardened = _harden_prediction_freshness(
        prediction,
        [Sample(timestamp=997.0, position_age_s=2.0)],
        now=1000.0,
    )
    assert hardened is prediction
