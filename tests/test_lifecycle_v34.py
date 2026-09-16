from types import SimpleNamespace
from app.intelligence.lifecycle import decide_lifecycle, prediction_changed


def p(**kw):
    defaults = dict(stale=False, already_passed=False, state="Approaching", enters_alert_radius=True, time_to_cpa_s=200)
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def test_candidate_prepare_ready_photo_passed():
    assert decide_lifecycle(p(), 400, 430).stage == "candidate"
    assert decide_lifecycle(p(), 240, 270).stage == "prepare"
    assert decide_lifecycle(p(), 90, 110).stage == "camera_ready"
    assert decide_lifecycle(p(), -2, 15).stage == "photo_now"
    assert decide_lifecycle(p(already_passed=True), 0, 0).stage == "passed"


def test_stale_and_outside_do_not_create_live_alert():
    assert not decide_lifecycle(p(stale=True)).should_have_live_message
    assert decide_lifecycle(p(enters_alert_radius=False)).stage == "detection"


def test_prediction_change_threshold():
    assert prediction_changed(4.2, 18.7, 15)
    assert not prediction_changed(4.2, 4.8, 15)
