from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app import decision_recorder as recorder
from app.agy_supervisor_goal_v44 import V44_SUPERVISOR_GOAL
from app.intelligence import transient_turn_guard_v44 as turn_guard
from app.intelligence.google_contrails import GoogleContrailsService
from app.intelligence.trajectory import HistorySample
from app.profile_miniapp import PROFILE_SETUP_HTML, _clean_config


def _sample(t: float, heading: float, age: float = 0.0) -> HistorySample:
    return HistorySample(
        timestamp=t,
        latitude=41.0,
        longitude=28.0,
        altitude_m=3200.0,
        speed_kts=230.0,
        heading_deg=heading,
        vertical_rate_mps=-4.0,
        position_age_s=age,
    )


def _prediction(*, stale: bool = False, current: float = 24.0, straight: float = 5.0):
    return SimpleNamespace(
        stale=stale,
        current_distance_km=current,
        projected_closest_km=straight,
        turn_rate_deg_s=0.8,
        time_to_cpa_s=90.0,
        confidence="High",
        confidence_score=0.9,
    )


def _route_result(destination: str = "IST", terminal: str = "TERMINAL_HIGH"):
    return SimpleNamespace(
        destination_code=destination,
        terminal_arrival_state=terminal,
        terminal_arrival_score=0.91,
        history_days=3,
        similar_days=3,
        similarity_km=1.4,
        historical_match_score=0.9,
        ensemble_pass_score=0.3,
        live_pass_score=0.2,
        qualification_state="PENDING_TERMINAL",
        reason="terminal evidence",
    )


def test_terminal_arrival_transient_alignment_is_suppressed(monkeypatch: pytest.MonkeyPatch) -> None:
    samples = [_sample(100, 250), _sample(105, 254), _sample(110, 258), _sample(115, 262)]
    monkeypatch.setattr(turn_guard, "_curved_cpas", lambda *a, **k: (27.0, 29.0, 27.0))

    evidence = turn_guard.assess_transient_alignment(
        ac=SimpleNamespace(callsign="THY100", icao24="4abc01"),
        pred=_prediction(),
        result=_route_result(),
        current_samples=samples,
        user_lat=41.1,
        user_lon=28.2,
        alert_radius_km=15.0,
        self_service=object(),
        now=115.0,
    )

    assert evidence.active is True
    assert evidence.reason_code == "terminal_arrival_transient_alignment"
    assert evidence.straight_cpa_km == 5.0
    assert evidence.curved_cpa_km == 27.0
    assert evidence.rate_deg_s > 0.12


def test_genuine_turn_toward_observer_is_not_delayed(monkeypatch: pytest.MonkeyPatch) -> None:
    samples = [_sample(100, 250), _sample(105, 254), _sample(110, 258), _sample(115, 262)]
    monkeypatch.setattr(turn_guard, "_curved_cpas", lambda *a, **k: (4.0, 4.5, 4.0))

    evidence = turn_guard.assess_transient_alignment(
        ac=SimpleNamespace(callsign="TEST1", icao24="4abc02"),
        pred=_prediction(),
        result=_route_result(destination="", terminal="NOT_TERMINAL"),
        current_samples=samples,
        user_lat=41.1,
        user_lon=28.2,
        alert_radius_km=15.0,
        self_service=object(),
        now=115.0,
    )

    assert evidence.active is False
    assert evidence.curved_cpa_km == 4.0


def test_stale_adsb_never_creates_transient_turn_authority(monkeypatch: pytest.MonkeyPatch) -> None:
    called = False

    def curved(*args, **kwargs):
        nonlocal called
        called = True
        return 30.0, 30.0, 30.0

    monkeypatch.setattr(turn_guard, "_curved_cpas", curved)
    evidence = turn_guard.assess_transient_alignment(
        ac=SimpleNamespace(),
        pred=_prediction(stale=True),
        result=_route_result(),
        current_samples=[_sample(100, 250), _sample(105, 255), _sample(110, 260)],
        user_lat=41.1,
        user_lon=28.2,
        alert_radius_km=15.0,
        self_service=object(),
        now=110.0,
    )
    assert evidence.active is False
    assert called is False


def test_decision_recorder_drops_diagnostics_instead_of_blocking(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder.reset_recorder_for_tests()
    queue: asyncio.Queue = asyncio.Queue(maxsize=1)
    monkeypatch.setattr(recorder, "_ensure_writer", lambda: queue)

    first = recorder.record_decision(
        subsystem="prediction", event="route", state_key="a", state="allow",
        evidence={"icao24": "one"}, force=True,
    )
    second = recorder.record_decision(
        subsystem="prediction", event="route", state_key="b", state="allow",
        evidence={"icao24": "two"}, force=True,
    )

    assert first.startswith("dec-")
    assert second == ""
    assert queue.qsize() == 1
    assert recorder.recorder_stats()["dropped"] == 1
    recorder.reset_recorder_for_tests()


def test_google_contrails_probability_is_not_fabricated_when_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.intelligence import google_contrails as module

    service = GoogleContrailsService()
    monkeypatch.setattr(module.settings, "google_contrails_api_key", "")
    assert service.get_or_schedule(41.0, 28.0, 10000.0) is None

    high = service._result(0.78, 330, "2026-09-19T17:00:00+00:00")
    assert high.probability == pytest.approx(0.78)
    assert high.source == "Google Contrails v2 forecast"
    assert high.formation == "Very Likely"


def test_visual_setup_preserves_inheritance_and_rejects_unknown_aircraft() -> None:
    existing = {
        "location": {"latitude": 41.0, "longitude": 28.0, "radius_km": 15},
        "preferences": {"spotting": {"mode": "Standard aviation"}},
        "camera": {},
    }
    raw = {
        "location": {"latitude": 41.1, "longitude": 28.2, "radius_km": 20},
        "preferences": {
            "spotting": {"mode": "Maximum detail"},
            "aircraft_filter": {
                "mode": "selected",
                "selected_categories": ["widebody"],
                "selected_types": ["A359", "NOTREAL"],
                "excluded_types": [],
            },
            "filter_rules": {
                "profile": {"max_altitude_ft": 40000},
                "categories": {"widebody": {"min_altitude_ft": 5000}},
                "aircraft": {"A359": {"radius_km": 12}, "NOTREAL": {"enabled": False}},
            },
        },
        "camera": {},
    }

    cleaned = _clean_config(raw, existing)
    selection = cleaned["preferences"]["aircraft_filter"]
    rules = cleaned["preferences"]["filter_rules"]
    assert selection["selected_types"] == ["A359"]
    assert rules["profile"] == {"max_altitude_ft": 40000.0}
    assert rules["categories"]["widebody"] == {"min_altitude_ft": 5000.0}
    assert rules["aircraft"]["A359"] == {"radius_km": 12.0}
    assert "NOTREAL" not in rules["aircraft"]
    assert cleaned["preferences"]["spotting"]["mode"] == "Maximum detail"


def test_visual_setup_uses_telegram_native_theme_and_safe_areas() -> None:
    assert "--tg-theme-bg-color" in PROFILE_SETUP_HTML
    assert "safe-area-inset-top" in PROFILE_SETUP_HTML
    assert "safe-area-inset-bottom" in PROFILE_SETUP_HTML
    assert "BackButton" in PROFILE_SETUP_HTML
    assert "Visual Setup" in PROFILE_SETUP_HTML
    assert "glassmorphism" not in PROFILE_SETUP_HTML.lower()


def test_agy_shadow_contract_has_zero_live_authority() -> None:
    text = V44_SUPERVISOR_GOAL.lower()
    assert "shadow opinion only" in text
    assert "live deterministic decision remains authoritative" in text
    assert "never modify alert decisions" in text
    assert "never use paid ai/api credits" in text
    assert "missing ads-b coverage" in text
