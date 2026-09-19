from __future__ import annotations

from app.worker import cadence_due_guard_v424 as guard
from app.worker import v36


def _user(delay_seconds: float = 5.0) -> dict:
    return {
        "user_id": 42,
        "admin_control": {
            "priority_enabled": True,
            "delay_seconds": delay_seconds,
            "notifications_enabled": True,
        },
    }


def test_new_user_is_immediately_due(monkeypatch):
    monkeypatch.setattr(v36, "_last_user_processed_mono", {})
    assert guard._is_due_jitter_safe(_user(), 100.0) is True


def test_five_second_user_is_not_skipped_by_small_scheduler_jitter(monkeypatch):
    monkeypatch.setattr(v36, "_last_user_processed_mono", {42: 100.0})
    assert guard._is_due_jitter_safe(_user(), 104.86) is True


def test_tolerance_does_not_turn_four_second_checks_into_due_checks(monkeypatch):
    monkeypatch.setattr(v36, "_last_user_processed_mono", {42: 100.0})
    assert guard._is_due_jitter_safe(_user(), 104.74) is False


def test_longer_custom_delay_only_gets_same_bounded_tolerance(monkeypatch):
    monkeypatch.setattr(v36, "_last_user_processed_mono", {42: 100.0})
    assert guard._is_due_jitter_safe(_user(30.0), 129.76) is True
    assert guard._is_due_jitter_safe(_user(30.0), 129.74) is False
