from __future__ import annotations

from datetime import datetime, timezone

from app.next_hour_shadow import _closest_point, _confidence, _median_time_of_day, _today_at_seconds


def test_median_time_of_day_handles_midnight_wrap():
    # 23:55, 00:05, 00:10 should cluster around midnight rather than noon.
    median, spread = _median_time_of_day([23 * 3600 + 55 * 60, 5 * 60, 10 * 60])
    assert median <= 15 * 60 or median >= 23 * 3600 + 45 * 60
    assert spread <= 15 * 60


def test_closest_point_returns_distance_and_timestamp():
    points = [
        {"lat": 41.0, "lon": 29.0, "t": 1000},
        {"lat": 41.05, "lon": 29.05, "t": 2000},
    ]
    result = _closest_point(points, 41.001, 29.001)
    assert result is not None
    distance, ts = result
    assert distance < 1.0
    assert ts == 1000


def test_confidence_requires_repeatability():
    assert _confidence(3, 500) == "High"
    assert _confidence(2, 1000) == "Medium"
    assert _confidence(1, 0) == "Low"
    assert _confidence(3, 2000) == "Low"


def test_today_at_seconds_uses_utc_day():
    now = datetime(2026, 9, 18, 22, 30, tzinfo=timezone.utc)
    result = _today_at_seconds(now, 3600)
    assert result == datetime(2026, 9, 18, 1, 0, tzinfo=timezone.utc)
