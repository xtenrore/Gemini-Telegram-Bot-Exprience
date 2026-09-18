from __future__ import annotations

import time
from types import SimpleNamespace

from app.admin.auth import make_delegated_admin_token, verify_delegated_admin_token
from app.config import settings
from app.worker import v36


def test_delegated_admin_token_is_signed_and_tamper_resistant(monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "unit-test-admin-secret")
    token = make_delegated_admin_token(123456)
    assert token.startswith("123456.")
    assert verify_delegated_admin_token(token) == 123456
    assert verify_delegated_admin_token(token + "x") is None
    assert verify_delegated_admin_token("654321." + token.split(".", 1)[1]) is None


def test_user_control_defaults_match_five_second_base(monkeypatch):
    monkeypatch.setattr(settings, "poll_interval_seconds", 5)
    control = v36._control({"user_id": 1})
    assert control == {
        "priority_enabled": False,
        "delay_seconds": 5.0,
        "notifications_enabled": True,
    }


def test_delay_is_bounded_and_priority_sorts_first():
    slow = {"user_id": 2, "admin_control": {"delay_seconds": 999}}
    priority = {"user_id": 3, "admin_control": {"priority_enabled": True, "delay_seconds": 15}}
    normal = {"user_id": 1, "admin_control": {"delay_seconds": 5}}

    assert v36._control(slow)["delay_seconds"] == 120.0
    ordered = sorted([slow, normal, priority], key=v36._user_order)
    assert [user["user_id"] for user in ordered] == [3, 1, 2]


def test_per_user_delay_defers_only_that_user(monkeypatch):
    monkeypatch.setattr(v36, "_last_user_processed_mono", {})
    user = {"user_id": 42, "admin_control": {"delay_seconds": 20}}
    now = time.monotonic()
    v36._last_user_processed_mono[42] = now - 10
    assert v36._is_due(user, now) is False
    assert v36._is_due(user, now + 11) is True


def test_priority_region_order_precedes_normal_region():
    priority_region = v36.v35.SharedPollRegion(
        key="priority",
        users=[{"user_id": 1, "admin_control": {"priority_enabled": True, "delay_seconds": 5}, "location": {}}],
        latitude=0,
        longitude=0,
        radius_nm=70,
    )
    normal_region = v36.v35.SharedPollRegion(
        key="normal",
        users=[{"user_id": 2, "admin_control": {"priority_enabled": False, "delay_seconds": 5}, "location": {}}],
        latitude=0,
        longitude=0,
        radius_nm=70,
    )
    assert v36._region_order(priority_region) < v36._region_order(normal_region)


def test_priority_region_promotion_forces_hot_window(monkeypatch):
    snapshot = SimpleNamespace(hot_until_mono=0.0)
    monkeypatch.setattr(v36.v35._shared_poller, "_snapshots", {"r1": snapshot})
    v36._promote_priority_region("r1", 100.0)
    assert snapshot.hot_until_mono == 100.0 + v36.v35.HOT_HOLD_S


def test_processing_delay_is_measured_from_evaluation_start_not_completion(monkeypatch):
    monkeypatch.setattr(v36, "_last_user_processed_mono", {})
    user = {"user_id": 42, "admin_control": {"delay_seconds": 5}}

    # Regression: an 11-second provider cycle used to store completion=111,
    # making the user wait until 116. The five-second cadence must be anchored
    # to start=100, so at completion the user is already due again.
    v36._record_users_processed([user], 100.0)
    assert v36._is_due(user, 111.0) is True


def test_later_region_becomes_due_while_earlier_region_is_busy(monkeypatch):
    monkeypatch.setattr(v36, "_last_user_processed_mono", {42: 100.0})
    user = {
        "user_id": 42,
        "admin_control": {"delay_seconds": 5},
        "location": {},
    }
    region = v36.v35.SharedPollRegion(
        key="later-region",
        users=[user],
        latitude=0.0,
        longitude=0.0,
        radius_nm=70,
    )

    due_early, deferred_early = v36._collect_due_users(region, 104.0)
    due_later, deferred_later = v36._collect_due_users(region, 106.0)

    assert due_early == []
    assert deferred_early == 1
    assert [item["user_id"] for item in due_later] == [42]
    assert deferred_later == 0
