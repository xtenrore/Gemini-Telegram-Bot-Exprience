"""AI-powered aircraft verification using Gemini + Groq.

Multi-model cascade with per-model daily quota tracking.
Used sparingly for:
  - Provider conflict resolution during learning
  - Dislike investigation
  - Location-change analysis
"""

from __future__ import annotations

import datetime
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# ── Model definitions ────────────────────────────────────────────────────────

@dataclass
class AIModel:
    """Configuration for a single AI model."""

    name: str
    provider: str  # "gemini" or "groq"
    rpm: int  # requests per minute
    rpd: int  # requests per day (0 = unlimited)
    model_id: str  # actual API model identifier

    # Runtime tracking
    daily_calls: int = 0
    last_call_time: float = 0.0
    last_reset_day: int = 0
    errors: int = 0


# Models ordered by preference (Groq first for higher RPM and reliability)
_GROQ_MODELS = [
    AIModel("groq-llama-3.3-70b", "groq", rpm=30, rpd=0,
            model_id="llama-3.3-70b-versatile"),
]

_GEMINI_MODELS = [
    AIModel("gemini-2.0-flash-lite", "gemini", rpm=15, rpd=1500,
            model_id="gemini-2.0-flash-lite"),
    AIModel("gemini-2.0-flash", "gemini", rpm=15, rpd=1500,
            model_id="gemini-2.0-flash"),
]


class AIJudge:
    """Multi-model AI cascade with quota management.

    Automatically cascades through available models when quotas are hit.
    Tracks usage per model per day to prevent going over limits.
    """

    def __init__(self) -> None:
        self._models: list[AIModel] = []
        self._initialized = False
        self._last_call_time = 0.0

    def initialize(self) -> None:
        """Set up available models based on configured API keys."""
        self._models = []

        if settings.groq_api_key:
            self._models.extend(_GROQ_MODELS)
            logger.info("AI Judge: Groq primary model available (%s)", settings.groq_model)

        if settings.gemini_api_key:
            self._models.extend(_GEMINI_MODELS)
            logger.info("AI Judge: %d Gemini fallback model(s) available", len(_GEMINI_MODELS))

        if not self._models:
            logger.warning("AI Judge: No AI API keys configured — AI features disabled")

        self._initialized = True
        total_daily = sum(m.rpd for m in self._models if m.rpd > 0)
        logger.info(
            "AI Judge initialized: %d model(s), ~%d pooled daily calls",
            len(self._models),
            total_daily,
        )

    def _maybe_reset_daily(self) -> None:
        """Reset daily counters if we crossed into a new UTC day."""
        today = datetime.datetime.now(datetime.timezone.utc).timetuple().tm_yday
        for model in self._models:
            if model.last_reset_day != today:
                model.daily_calls = 0
                model.errors = 0
                model.last_reset_day = today

    def _find_available_model(self) -> AIModel | None:
        """Find the first model that hasn't exhausted its daily quota."""
        self._maybe_reset_daily()

        for model in self._models:
            # Check daily limit (with 10% safety buffer)
            if model.rpd > 0:
                safe_limit = int(model.rpd * 0.9)
                if model.daily_calls >= safe_limit:
                    continue

            # Check per-minute rate (simple: enforce 60/rpm seconds between calls)
            if model.rpm > 0 and model.last_call_time > 0:
                min_interval = 60.0 / model.rpm
                if time.monotonic() - model.last_call_time < min_interval:
                    continue

            return model

        return None

    def can_call(self) -> bool:
        """Check if any AI model has remaining quota."""
        if not self._initialized or not self._models:
            return False
        return self._find_available_model() is not None

    async def judge_conflict(
        self,
        icao24: str,
        aircraft_type: str,
        lat: float,
        lon: float,
        providers_reporting: list[str],
        providers_missing: list[str],
        user_lat: float,
        user_lon: float,
        radius_km: float,
    ) -> str:
        """Ask AI whether a plane detection is real or phantom.

        Returns "REAL", "FALSE", or "UNKNOWN" (if AI unavailable).
        """
        prompt = (
            f"Aircraft verification task. Answer with ONLY one word: REAL or FALSE.\n\n"
            f"Aircraft ICAO24: {icao24}\n"
            f"Type: {aircraft_type or 'unknown'}\n"
            f"Position: {lat:.4f}, {lon:.4f}\n"
            f"Reported by: {', '.join(providers_reporting)}\n"
            f"NOT reported by: {', '.join(providers_missing)}\n"
            f"User location: {user_lat:.4f}, {user_lon:.4f}\n"
            f"Monitoring radius: {radius_km:.0f}km\n\n"
            f"Is this aircraft likely a real detection or a false positive? "
            f"Consider: number of confirming sources, whether the position "
            f"is within range, and typical ADS-B coverage patterns."
        )
        return await self._call_ai(prompt)

    async def analyze_dislike(
        self,
        icao24: str,
        aircraft_type: str,
        distance_km: float,
        providers_reporting: list[str],
        user_feedback: str,
    ) -> str:
        """Analyze why a user disliked a notification.

        Returns a brief analysis string.
        """
        prompt = (
            f"Analyze this aircraft notification that a user disliked.\n\n"
            f"Aircraft ICAO24: {icao24}\n"
            f"Type: {aircraft_type}\n"
            f"Distance from user: {distance_km:.1f}km\n"
            f"Providers that reported it: {', '.join(providers_reporting)}\n"
            f"User feedback: {user_feedback}\n\n"
            f"In 1-2 sentences, explain the most likely reason this was a bad "
            f"notification (e.g., false detection, wrong location, duplicate, "
            f"provider reliability issue). Be specific."
        )
        return await self._call_ai(prompt)

    async def _call_ai(self, prompt: str) -> str:
        """Make an AI call using the cascade, return response text."""
        model = self._find_available_model()
        if model is None:
            logger.warning("AI Judge: All models exhausted, falling back to UNKNOWN")
            return "UNKNOWN"

        try:
            if model.provider == "gemini":
                result = await self._call_gemini(model, prompt)
            elif model.provider == "groq":
                result = await self._call_groq(model, prompt)
            else:
                return "UNKNOWN"

            model.daily_calls += 1
            model.last_call_time = time.monotonic()

            logger.debug(
                "AI call (%s): %d/%s daily budget used",
                model.name,
                model.daily_calls,
                model.rpd if model.rpd > 0 else "∞",
            )
            return result.strip()

        except Exception as exc:
            model.errors += 1
            model.daily_calls += 1  # Count failed attempts too
            model.last_call_time = time.monotonic()
            logger.warning("AI call failed (%s): %s", model.name, exc)

            # Try next model in cascade
            next_model = self._find_available_model()
            if next_model and next_model != model:
                logger.info("Cascading to %s", next_model.name)
                try:
                    if next_model.provider == "gemini":
                        result = await self._call_gemini(next_model, prompt)
                    else:
                        result = await self._call_groq(next_model, prompt)
                    next_model.daily_calls += 1
                    next_model.last_call_time = time.monotonic()
                    return result.strip()
                except Exception as exc2:
                    next_model.errors += 1
                    next_model.daily_calls += 1
                    logger.warning("Cascade also failed (%s): %s", next_model.name, exc2)

            return "UNKNOWN"

    async def _call_gemini(self, model: AIModel, prompt: str) -> str:
        """Call the Google Gemini API."""
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/"
            f"models/{model.model_id}:generateContent"
            f"?key={settings.gemini_api_key}"
        )
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "maxOutputTokens": 50,
                "temperature": 0.1,
            },
        }

        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()

        # Extract text from response
        candidates = data.get("candidates", [])
        if candidates:
            parts = candidates[0].get("content", {}).get("parts", [])
            if parts:
                return parts[0].get("text", "UNKNOWN")
        return "UNKNOWN"

    async def _call_groq(self, model: AIModel, prompt: str) -> str:
        """Call the Groq API (OpenAI-compatible)."""
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {settings.groq_api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model.model_id,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 50,
            "temperature": 0.1,
        }

        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        choices = data.get("choices", [])
        if choices:
            return choices[0].get("message", {}).get("content", "UNKNOWN")
        return "UNKNOWN"

    def get_usage_report(self) -> dict[str, Any]:
        """Return usage stats for all models (admin dashboard)."""
        self._maybe_reset_daily()
        models_info = []
        for m in self._models:
            safe_limit = int(m.rpd * 0.9) if m.rpd > 0 else -1
            models_info.append({
                "name": m.name,
                "provider": m.provider,
                "daily_calls": m.daily_calls,
                "daily_limit": m.rpd,
                "safe_limit": safe_limit,
                "remaining": max(0, safe_limit - m.daily_calls) if safe_limit > 0 else -1,
                "errors": m.errors,
            })

        total_remaining = sum(
            max(0, int(m.rpd * 0.9) - m.daily_calls)
            for m in self._models if m.rpd > 0
        )

        return {
            "models": models_info,
            "total_remaining_today": total_remaining,
            "any_available": self.can_call(),
        }


# ── Module-level singleton ──────────────────────────────────────────────────
ai_judge = AIJudge()
