"""Tests for the optional AI verification cascade."""

from unittest.mock import AsyncMock

import pytest

from app.aircraft.ai_judge import AIJudge
from app.config import settings


def test_ai_judge_uses_configured_gemini_models(monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "test-key")
    monkeypatch.setattr(settings, "gemini_model_primary", "gemini-primary")
    monkeypatch.setattr(settings, "gemini_model_secondary", "gemini-secondary")
    monkeypatch.setattr(settings, "groq_api_key", "")

    judge = AIJudge()
    judge.initialize()

    assert [model.model_id for model in judge._models] == [
        "gemini-primary",
        "gemini-secondary",
    ]
    assert judge.can_call() is True


@pytest.mark.asyncio
async def test_conflict_result_is_strictly_normalized(monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "test-key")
    monkeypatch.setattr(settings, "gemini_model_primary", "gemini-primary")
    monkeypatch.setattr(settings, "gemini_model_secondary", "")
    monkeypatch.setattr(settings, "groq_api_key", "")

    judge = AIJudge()
    judge.initialize()
    judge._call_ai = AsyncMock(return_value="REAL\nextra text")

    result = await judge.judge_conflict(
        icao24="abc123",
        aircraft_type="B738",
        lat=41.0,
        lon=29.0,
        providers_reporting=["adsb.lol", "adsb.fi"],
        providers_missing=[],
        user_lat=41.01,
        user_lon=29.01,
        radius_km=15.0,
    )

    assert result == "REAL"
