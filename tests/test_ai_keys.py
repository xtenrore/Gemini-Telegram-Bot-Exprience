"""Regression tests for Gemini/Groq credential pools."""

from app.ai_keys import build_gemini_key_pool, build_groq_key_pool
from app.config import settings


def test_gemini_pool_rotates_after_rate_limit(monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "g-one")
    monkeypatch.setattr(settings, "gemini_api_key_2", "g-two")

    pool = build_gemini_key_pool()
    first, second = pool.candidates()
    assert first.label == "GEMINI_API_KEY"
    assert second.label == "GEMINI_API_KEY_2"

    pool.record_attempt(first)
    pool.mark_http_failure(first, 429, retry_after="120")

    candidates = pool.candidates()
    assert [state.label for state in candidates] == ["GEMINI_API_KEY_2"]
    report = pool.report()
    assert report[0]["available"] is False
    assert report[0]["last_status"] == 429


def test_groq_pool_prefers_new_names_and_deduplicates_legacy(monkeypatch):
    monkeypatch.setattr(settings, "groq_key", "groq-one")
    monkeypatch.setattr(settings, "groq_key_2", "groq-two")
    monkeypatch.setattr(settings, "groq_api_key", "groq-one")

    pool = build_groq_key_pool()
    assert [state.label for state in pool.candidates()] == [
        "GROQ_KEY",
        "GROQ_KEY_2",
    ]


def test_success_round_robins_between_healthy_keys(monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "g-one")
    monkeypatch.setattr(settings, "gemini_api_key_2", "g-two")

    pool = build_gemini_key_pool()
    first = pool.candidates()[0]
    pool.record_attempt(first)
    pool.mark_success(first)

    assert pool.candidates()[0].label == "GEMINI_API_KEY_2"
