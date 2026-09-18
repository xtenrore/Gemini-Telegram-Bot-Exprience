from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.bot.next60 import render_next60
from app.sentinel_network import EUROPE_SENTINELS
from app.sentinel_shadow import derive_turn_away_traps


def test_next60_renders_all_horizon_buckets_and_shadow_warning():
    now = datetime(2026, 9, 18, 16, 0, tzinfo=timezone.utc)
    docs = []
    for minutes, callsign in ((8, "THY1"), (22, "BAW2"), (48, "DLH3")):
        predicted = now + timedelta(minutes=minutes)
        docs.append({
            "callsign": callsign,
            "predicted_cpa_at": predicted,
            "window_start": predicted - timedelta(minutes=8),
            "window_end": predicted + timedelta(minutes=8),
            "prediction_horizon_s": minutes * 60,
            "predicted_closest_km": 8.5,
            "historical_days": 3,
            "confidence": "High",
        })
    text = render_next60(now, docs)
    assert "0–15 min" in text
    assert "15–30 min" in text
    assert "30–60 min" in text
    assert "THY1" in text and "BAW2" in text and "DLH3" in text
    assert "shadow/history-based" in text


def test_europe_sentinel_network_is_spread_and_unique():
    assert len(EUROPE_SENTINELS) >= 8
    ids = {region.id for region in EUROPE_SENTINELS}
    assert len(ids) == len(EUROPE_SENTINELS)
    longitudes = [region.longitude for region in EUROPE_SENTINELS]
    latitudes = [region.latitude for region in EUROPE_SENTINELS]
    assert max(longitudes) - min(longitudes) > 20
    assert max(latitudes) - min(latitudes) > 8


def test_adversarial_turn_away_trap_is_generated_for_strong_turn():
    points = [
        {"t": 1, "lat": 0.00, "lon": 0.00},
        {"t": 2, "lat": 0.00, "lon": 0.05},
        {"t": 3, "lat": 0.00, "lon": 0.10},
        {"t": 4, "lat": 0.05, "lon": 0.10},
        {"t": 5, "lat": 0.10, "lon": 0.10},
        {"t": 6, "lat": 0.15, "lon": 0.10},
    ]
    traps = derive_turn_away_traps(points)
    assert traps
    trap = traps[0]
    assert trap["turn_angle_deg"] >= 45
    assert trap["actual_closest_km"] > 18
