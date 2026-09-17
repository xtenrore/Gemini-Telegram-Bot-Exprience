"""Gemini-powered camera understanding and photographic recommendations.

v3.2 intentionally keeps Gemini in the decision-making loop. Deterministic code
collects measurements (weather, air quality, sun geometry); Gemini interprets
those facts for the user's specific camera/lens and produces the settings.
"""
from __future__ import annotations

import json
import logging
from typing import Any, TypeVar
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ValidationError

from app.ai_keys import build_gemini_key_pool
from app.config import settings
from app.photography.models import CameraProfile, LensProfile, PhotoRecommendation, PhotographyContext

logger = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)
_GENERATE_CONTENT_BASE = "https://generativelanguage.googleapis.com/v1beta/models"


class GeminiPhotographyError(RuntimeError):
    """Raised when Gemini cannot produce a safe, structured photography result."""


def _extract_output_text(payload: dict[str, Any]) -> str:
    """Extract text from generateContent, while retaining old Interactions support."""
    chunks: list[str] = []

    for candidate in payload.get("candidates") or []:
        if not isinstance(candidate, dict):
            continue
        content = candidate.get("content") or {}
        if not isinstance(content, dict):
            continue
        for part in content.get("parts") or []:
            if isinstance(part, dict) and isinstance(part.get("text"), str) and part["text"].strip():
                chunks.append(part["text"])
    if chunks:
        return "".join(chunks).strip()

    for step in payload.get("steps") or []:
        if not isinstance(step, dict) or step.get("type") != "model_output":
            continue
        for item in step.get("content") or []:
            if isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text")
                if text:
                    chunks.append(str(text))
    if chunks:
        return "".join(chunks).strip()
    for key in ("output_text", "text"):
        if isinstance(payload.get(key), str) and payload[key].strip():
            return payload[key].strip()
    raise GeminiPhotographyError("Gemini returned no text output")


def _parse_json_text(text: str) -> dict[str, Any]:
    clean = text.strip()
    if clean.startswith("```"):
        lines = clean.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        clean = "\n".join(lines).strip()
    try:
        value = json.loads(clean)
    except json.JSONDecodeError as exc:
        raise GeminiPhotographyError("Gemini returned malformed JSON") from exc
    if not isinstance(value, dict):
        raise GeminiPhotographyError("Gemini returned a non-object JSON result")
    return value


def _gemini_json_schema(model_cls: type[BaseModel]) -> dict[str, Any]:
    """Return Pydantic JSON schema with unsupported generation-only metadata removed."""
    schema = model_cls.model_json_schema()

    def clean(value: Any) -> Any:
        if isinstance(value, list):
            return [clean(item) for item in value]
        if not isinstance(value, dict):
            return value
        return {key: clean(item) for key, item in value.items() if key not in {"default", "examples"}}

    return clean(schema)


class GeminiPhotographyEngine:
    """Structured Gemini client specialized for aviation photography."""

    def __init__(self) -> None:
        self._models = [m for m in (settings.gemini_photo_model.strip(), settings.gemini_photo_fallback_model.strip()) if m]
        self._models = list(dict.fromkeys(self._models))
        self._keys = build_gemini_key_pool()

    @property
    def enabled(self) -> bool:
        return bool(self._keys.configured and self._models)

    async def _structured(
        self,
        prompt: str,
        model_cls: type[T],
        *,
        thinking_level: str = "medium",
        fast_first: bool = False,
    ) -> tuple[T, str]:
        """Run a single-turn structured request using Gemini generateContent.

        Key-specific auth/quota failures rotate to the next configured Gemini key.
        Model/schema failures switch model instead of burning every API key.
        """
        if not self.enabled:
            raise GeminiPhotographyError("No Gemini API key is configured")

        models = list(reversed(self._models)) if fast_first and len(self._models) > 1 else self._models
        last_error: Exception | None = None
        schema = _gemini_json_schema(model_cls)
        timeout_seconds = min(settings.gemini_photo_timeout_seconds, 15.0) if fast_first else settings.gemini_photo_timeout_seconds

        for model_id in models:
            endpoint = f"{_GENERATE_CONTENT_BASE}/{quote(model_id, safe='')}:generateContent"
            payload = {
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {
                    "thinkingConfig": {"thinkingLevel": thinking_level},
                    "responseMimeType": "application/json",
                    "responseJsonSchema": schema,
                },
            }
            key_candidates = self._keys.candidates()
            if not key_candidates:
                last_error = GeminiPhotographyError("All Gemini API keys are cooling down")
                break

            switch_model = False
            for key_state in key_candidates:
                headers = {"x-goog-api-key": key_state.key, "Content-Type": "application/json"}
                self._keys.record_attempt(key_state)
                try:
                    timeout = httpx.Timeout(timeout_seconds, connect=min(5.0, timeout_seconds))
                    async with httpx.AsyncClient(timeout=timeout) as client:
                        response = await client.post(endpoint, headers=headers, json=payload)
                    response.raise_for_status()
                    result = model_cls.model_validate(
                        _parse_json_text(_extract_output_text(response.json()))
                    )
                    self._keys.mark_success(key_state)
                    logger.info(
                        "Gemini photography model %s succeeded via %s",
                        model_id,
                        key_state.label,
                    )
                    return result, model_id
                except httpx.HTTPStatusError as exc:
                    last_error = exc
                    status = exc.response.status_code
                    if status in (401, 403, 429):
                        self._keys.mark_http_failure(
                            key_state,
                            status,
                            retry_after=exc.response.headers.get("Retry-After"),
                        )
                        logger.warning(
                            "Gemini photography credential %s failed with HTTP %s; rotating key",
                            key_state.label,
                            status,
                        )
                        continue
                    switch_model = True
                    logger.warning(
                        "Gemini photography model %s failed with HTTP %s; trying model fallback",
                        model_id,
                        status,
                    )
                    break
                except (ValidationError, ValueError, KeyError, TypeError, GeminiPhotographyError) as exc:
                    last_error = exc
                    switch_model = True
                    logger.warning(
                        "Gemini photography model %s returned unusable output (%s); trying model fallback",
                        model_id,
                        type(exc).__name__,
                    )
                    break
                except httpx.HTTPError as exc:
                    last_error = exc
                    switch_model = True
                    logger.warning(
                        "Gemini photography model %s network failure (%s); trying model fallback",
                        model_id,
                        type(exc).__name__,
                    )
                    break

            if switch_model:
                continue

        raise GeminiPhotographyError(
            f"All Gemini photography models/keys failed: {type(last_error).__name__ if last_error else 'unknown error'}"
        )

    async def resolve_camera(self, user_text: str) -> CameraProfile:
        prompt = f'''You are the camera-identification component of an aviation photography assistant.
The user typed this camera body description: {user_text!r}
Resolve the body accurately for fast-moving aircraft photography.
Never invent exact specifications when uncertain: use null, lower confidence, and explain ambiguity in assumptions.
Normalize brand/model but preserve raw_input. camera_type should be mirrorless, DSLR, compact, bridge, phone, action camera, or unknown.
Mention autofocus capabilities only when reasonably confident. confidence is identification confidence, not camera quality. Do not give shooting settings yet.'''
        profile, _ = await self._structured(prompt, CameraProfile, thinking_level="low", fast_first=True)
        if not profile.raw_input:
            profile.raw_input = user_text
        return profile

    async def resolve_lens(self, user_text: str, camera: CameraProfile | None = None) -> LensProfile:
        camera_hint = camera.model_dump_json(exclude_none=True) if camera else "unknown camera"
        prompt = f'''You are the lens-identification component of an aviation photography assistant.
Camera context: {camera_hint}
User lens description: {user_text!r}
Resolve the lens conservatively. Never invent exact focal/aperture/stabilization specifications when unsure; use null and assumptions.
Normalize brand/model, preserve raw_input, handle built-in bridge/phone lenses honestly, and set confidence 0..1. Do not recommend settings yet.'''
        profile, _ = await self._structured(prompt, LensProfile, thinking_level="low", fast_first=True)
        if not profile.raw_input:
            profile.raw_input = user_text
        return profile

    async def recommend(self, context: PhotographyContext) -> PhotoRecommendation:
        measured = json.dumps(context.as_prompt_payload(), ensure_ascii=False, indent=2)
        aircraft = context.aircraft
        eta = aircraft.eta_seconds if aircraft else None
        if eta is not None and eta <= 120:
            thinking_level = "low"
            urgency = "URGENT: the aircraft is expected within about two minutes. Return the primary usable setup immediately; minimize deliberation and prose."
        elif eta is not None and eta <= 300:
            thinking_level = "medium"
            urgency = "The aircraft is approaching within about five minutes. Favor a fast, decisive setup over lengthy analysis."
        else:
            thinking_level = "high"
            urgency = "There is enough time for a deeper photographic assessment."

        prompt = f'''You are a senior aviation and airshow photographer. Produce one precise, practical camera setup for the measured conditions below.
Gemini must make the photographic decision; the surrounding program only collects measurements.
{urgency}

MEASURED CONTEXT (do not alter or invent these facts):
{measured}

Decision principles:
- Prioritize freezing aircraft motion and maintaining autofocus reliability.
- Adapt to the exact camera/lens only when identification confidence supports it; otherwise use generic controls and say so.
- Heat haze is optical/atmospheric: faster shutter does not remove it. If strong, prefer shorter distance, higher sight-lines away from hot surfaces, or better timing.
- Use sun elevation, azimuth, subject bearing and lighting relationship; consider backlight, low sun, cloud diffusion and dynamic range.
- Consider visibility, aerosol optical depth, dust, PM, humidity, precipitation, wind/gusts, ground-air temperature difference and solar radiation.
- For live aircraft consider distance, speed, heading, ETA and angular motion. Without live aircraft, state advice is a baseline for the next aircraft.
- Give ONE primary setup. Auto ISO with a sensible ceiling is allowed. Values must be realistic for the identified gear.
- In poor light prefer a usable image over unrealistically low ISO. Only recommend panning when intentional; otherwise freeze motion.
- Stabilization does not freeze subject motion. Keep technique/warnings concise enough for Telegram.
- quality_score is the shooting opportunity right now, not camera quality. confidence is confidence in the recommendation.'''
        result, model_id = await self._structured(prompt, PhotoRecommendation, thinking_level=thinking_level)
        result.model_used = model_id
        return result


photography_ai = GeminiPhotographyEngine()
