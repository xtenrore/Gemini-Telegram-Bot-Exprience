"""AI-assisted aircraft verification using Gemini with optional Groq fallback.

The AI layer is deliberately non-critical: monitoring continues when every AI
provider is unavailable. Models are configured through environment variables so
retired model IDs can be changed without code changes.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass
class AIModel:
    name: str
    provider: str
    model_id: str
    calls: int = 0
    errors: int = 0
    cooldown_until: float = 0.0

    @property
    def available(self) -> bool:
        return time.monotonic() >= self.cooldown_until


class AIJudge:
    """Small, resilient model cascade for optional verification tasks."""

    def __init__(self) -> None:
        self._models: list[AIModel] = []
        self._initialized = False

    def initialize(self) -> None:
        self._models = []

        if settings.gemini_api_key.strip():
            seen: set[str] = set()
            for model_id in (
                settings.gemini_model_primary.strip(),
                settings.gemini_model_secondary.strip(),
            ):
                if model_id and model_id not in seen:
                    self._models.append(
                        AIModel(name=model_id, provider="gemini", model_id=model_id)
                    )
                    seen.add(model_id)

        if settings.groq_api_key.strip() and settings.groq_model.strip():
            self._models.append(
                AIModel(
                    name=settings.groq_model.strip(),
                    provider="groq",
                    model_id=settings.groq_model.strip(),
                )
            )

        self._initialized = True
        if self._models:
            logger.info(
                "AI Judge initialized with model cascade: %s",
                " -> ".join(model.name for model in self._models),
            )
        else:
            logger.info("AI Judge disabled: no AI API key configured")

    def _available_models(self) -> list[AIModel]:
        if not self._initialized:
            self.initialize()
        return [model for model in self._models if model.available]

    def can_call(self) -> bool:
        return bool(self._available_models())

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
        prompt = (
            "Aircraft verification task. Return exactly one label: REAL or FALSE.\n\n"
            f"Aircraft ICAO24: {icao24}\n"
            f"Type: {aircraft_type or 'unknown'}\n"
            f"Position: {lat:.4f}, {lon:.4f}\n"
            f"Reported by: {', '.join(providers_reporting) or 'none'}\n"
            f"Not reported by: {', '.join(providers_missing) or 'none'}\n"
            f"User location: {user_lat:.4f}, {user_lon:.4f}\n"
            f"Monitoring radius: {radius_km:.1f} km\n\n"
            "Judge whether this is likely a real ADS-B detection. Do not add explanation."
        )
        result = (await self._call_ai(prompt)).strip().upper()
        if result.startswith("REAL"):
            return "REAL"
        if result.startswith("FALSE"):
            return "FALSE"
        return "UNKNOWN"

    async def analyze_dislike(
        self,
        icao24: str,
        aircraft_type: str,
        distance_km: float,
        providers_reporting: list[str],
        user_feedback: str,
    ) -> str:
        prompt = (
            "Analyze why this aircraft alert may have been unhelpful. Keep the answer to "
            "one or two concise sentences.\n\n"
            f"ICAO24: {icao24}\n"
            f"Type: {aircraft_type or 'unknown'}\n"
            f"Distance: {distance_km:.1f} km\n"
            f"Providers: {', '.join(providers_reporting) or 'unknown'}\n"
            f"Feedback: {user_feedback}"
        )
        result = (await self._call_ai(prompt)).strip()
        return result or "UNKNOWN"

    async def _call_ai(self, prompt: str) -> str:
        models = self._available_models()
        if not models:
            return "UNKNOWN"

        for model in models:
            try:
                if model.provider == "gemini":
                    result = await self._call_gemini(model, prompt)
                else:
                    result = await self._call_groq(model, prompt)
                model.calls += 1
                if result.strip():
                    return result.strip()
            except httpx.HTTPStatusError as exc:
                model.errors += 1
                model.calls += 1
                self._apply_http_backoff(model, exc.response)
                logger.warning(
                    "AI model %s failed with HTTP %s; trying fallback",
                    model.name,
                    exc.response.status_code,
                )
            except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                model.errors += 1
                model.calls += 1
                model.cooldown_until = max(model.cooldown_until, time.monotonic() + 10.0)
                logger.warning("AI model %s failed (%s); trying fallback", model.name, exc)
            except Exception:
                model.errors += 1
                model.calls += 1
                model.cooldown_until = max(model.cooldown_until, time.monotonic() + 10.0)
                logger.exception("Unexpected AI model failure for %s", model.name)

        return "UNKNOWN"

    @staticmethod
    def _apply_http_backoff(model: AIModel, response: httpx.Response) -> None:
        status = response.status_code
        if status == 429:
            try:
                retry_after = float(response.headers.get("Retry-After", "60"))
            except ValueError:
                retry_after = 60.0
            model.cooldown_until = time.monotonic() + min(max(retry_after, 5.0), 300.0)
        elif status >= 500:
            model.cooldown_until = time.monotonic() + 15.0
        elif status in (401, 403, 404):
            # Bad key, disabled API, or retired model: avoid hammering it.
            model.cooldown_until = time.monotonic() + 300.0
        else:
            model.cooldown_until = time.monotonic() + 10.0

    async def _call_gemini(self, model: AIModel, prompt: str) -> str:
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model.model_id}:generateContent"
        )
        headers = {
            "x-goog-api-key": settings.gemini_api_key,
            "Content-Type": "application/json",
        }
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "maxOutputTokens": 96,
                "temperature": 0.0,
            },
        }

        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()

        candidates = data.get("candidates") or []
        if not candidates:
            raise ValueError("Gemini returned no candidates")
        parts = candidates[0].get("content", {}).get("parts") or []
        text = "".join(str(part.get("text") or "") for part in parts if isinstance(part, dict))
        if not text.strip():
            raise ValueError("Gemini returned an empty response")
        return text

    async def _call_groq(self, model: AIModel, prompt: str) -> str:
        headers = {
            "Authorization": f"Bearer {settings.groq_api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model.model_id,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 96,
            "temperature": 0.0,
        }
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
            response = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
            data = response.json()

        choices = data.get("choices") or []
        if not choices:
            raise ValueError("Groq returned no choices")
        text = choices[0].get("message", {}).get("content") or ""
        if not str(text).strip():
            raise ValueError("Groq returned an empty response")
        return str(text)

    def get_usage_report(self) -> dict[str, Any]:
        now = time.monotonic()
        return {
            "models": [
                {
                    "name": model.name,
                    "provider": model.provider,
                    "calls": model.calls,
                    "errors": model.errors,
                    "available": now >= model.cooldown_until,
                    "cooldown_seconds": round(max(0.0, model.cooldown_until - now), 1),
                }
                for model in self._models
            ],
            "any_available": self.can_call(),
        }


ai_judge = AIJudge()
