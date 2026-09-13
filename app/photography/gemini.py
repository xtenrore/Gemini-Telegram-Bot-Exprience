"""Gemini-powered camera understanding and photographic recommendations.

v3.2 intentionally keeps Gemini in the decision-making loop. Deterministic code
collects measurements (weather, air quality, sun geometry); Gemini interprets
those facts for the user's specific camera/lens and produces the settings.
"""
from __future__ import annotations

import json
import logging
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from app.config import settings
from app.photography.models import CameraProfile, LensProfile, PhotoRecommendation, PhotographyContext

logger = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)
_INTERACTIONS_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"


class GeminiPhotographyError(RuntimeError):
    """Raised when Gemini cannot produce a safe, structured photography result."""


def _extract_output_text(payload: dict[str, Any]) -> str:
    chunks: list[str] = []
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


class GeminiPhotographyEngine:
    """Structured Gemini client specialized for aviation photography."""

    def __init__(self) -> None:
        self._models = [m for m in (settings.gemini_photo_model.strip(), settings.gemini_photo_fallback_model.strip()) if m]
        self._models = list(dict.fromkeys(self._models))

    @property
    def enabled(self) -> bool:
        return bool(settings.gemini_api_key.strip() and self._models)

    async def _structured(self, prompt: str, model_cls: type[T], *, thinking_level: str = "medium") -> tuple[T, str]:
        if not self.enabled:
            raise GeminiPhotographyError("GEMINI_API_KEY is not configured")
        last_error: Exception | None = None
        for model_id in self._models:
            payload = {
                "model": model_id,
                "store": False,
                "input": prompt,
                "generation_config": {"thinking_level": thinking_level, "temperature": 0.2},
                "response_format": {
                    "type": "text",
                    "mime_type": "application/json",
                    "schema": model_cls.model_json_schema(),
                },
            }
            headers = {"x-goog-api-key": settings.gemini_api_key, "Content-Type": "application/json"}
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(settings.gemini_photo_timeout_seconds)) as client:
                    response = await client.post(_INTERACTIONS_URL, headers=headers, json=payload)
                response.raise_for_status()
                result = model_cls.model_validate(_parse_json_text(_extract_output_text(response.json())))
                return result, model_id
            except (httpx.HTTPError, ValidationError, ValueError, KeyError, TypeError, GeminiPhotographyError) as exc:
                last_error = exc
                status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
                logger.warning("Gemini photography model %s failed%s; trying fallback if available", model_id, f" (HTTP {status})" if status else "")
        raise GeminiPhotographyError(f"All Gemini photography models failed: {type(last_error).__name__ if last_error else 'unknown error'}")

    async def resolve_camera(self, user_text: str) -> CameraProfile:
        prompt = f'''You are the camera-identification component of an aviation photography assistant.
The user typed this camera body description: {user_text!r}
Resolve the body accurately for fast-moving aircraft photography.
Never invent exact specifications when uncertain: use null, lower confidence, and explain ambiguity in assumptions.
Normalize brand/model but preserve raw_input. camera_type should be mirrorless, DSLR, compact, bridge, phone, action camera, or unknown.
Mention autofocus capabilities only when reasonably confident. confidence is identification confidence, not camera quality. Do not give shooting settings yet.'''
        profile, _ = await self._structured(prompt, CameraProfile, thinking_level="low")
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
        profile, _ = await self._structured(prompt, LensProfile, thinking_level="low")
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
