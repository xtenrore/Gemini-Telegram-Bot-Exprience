from __future__ import annotations

from datetime import datetime, timedelta, timezone

from PIL import Image

from app.bot.forecast_card import HEIGHT, WIDTH, render_forecast_card


def test_forecast_card_renders_valid_png() -> None:
    now = datetime(2026, 9, 18, 16, 0, tzinfo=timezone.utc)
    docs = [
        {
            "callsign": "THY1017",
            "predicted_cpa_at": now + timedelta(minutes=8),
            "prediction_horizon_s": 8 * 60,
            "predicted_closest_km": 4.2,
            "confidence": "High",
            "stage": "approaching",
            "source": "live",
        },
        {
            "callsign": "DLH1304",
            "predicted_cpa_at": now + timedelta(minutes=24),
            "prediction_horizon_s": 24 * 60,
            "predicted_closest_km": 9.8,
            "confidence": "Medium",
            "historical_days": 2,
            "source": "history",
        },
        {
            "callsign": "BAW680",
            "predicted_cpa_at": now + timedelta(minutes=47),
            "prediction_horizon_s": 47 * 60,
            "predicted_closest_km": 11.4,
            "confidence": "Low",
            "historical_days": 1,
            "source": "history",
        },
    ]

    output = render_forecast_card(now, docs)
    assert output.read(8) == b"\x89PNG\r\n\x1a\n"
    output.seek(0)
    image = Image.open(output)
    assert image.size == (WIDTH, HEIGHT)
    assert image.mode == "RGB"


def test_forecast_card_handles_empty_forecast() -> None:
    now = datetime(2026, 9, 18, 16, 0, tzinfo=timezone.utc)
    output = render_forecast_card(now, [])
    assert len(output.getvalue()) > 10_000
