from types import SimpleNamespace

from app.intelligence.camera import _practical_standard_shutter
from app.intelligence.lifecycle import should_cancel_active_alert


def test_active_alert_not_cancelled_when_cpa_is_still_inside_radius():
    pred = SimpleNamespace(
        stale=False,
        already_passed=False,
        state="Approaching",
        enters_alert_radius=True,
        projected_closest_km=5.3,
    )
    assert should_cancel_active_alert(pred, 5.3, 15.0) is False


def test_active_alert_not_cancelled_on_small_outside_radius_wobble():
    pred = SimpleNamespace(
        stale=False,
        already_passed=False,
        state="Prediction uncertain",
        enters_alert_radius=False,
        projected_closest_km=15.8,
    )
    assert should_cancel_active_alert(pred, 14.9, 15.0) is False


def test_active_alert_cancelled_only_after_meaningful_turn_away():
    pred = SimpleNamespace(
        stale=False,
        already_passed=False,
        state="Turning away",
        enters_alert_radius=False,
        projected_closest_km=22.0,
    )
    assert should_cancel_active_alert(pred, 5.3, 15.0) is True


def test_standard_aviation_low_altitude_defaults_to_1_1000():
    point = SimpleNamespace(altitude_m=1800.0)
    floor, preferred = _practical_standard_shutter(2500, 4000, point, "Standard aviation")
    assert floor == 1000
    assert preferred == 1000


def test_maximum_detail_can_still_use_faster_shutter():
    point = SimpleNamespace(altitude_m=1800.0)
    floor, preferred = _practical_standard_shutter(2500, 4000, point, "Maximum detail")
    assert (floor, preferred) == (2500, 4000)
