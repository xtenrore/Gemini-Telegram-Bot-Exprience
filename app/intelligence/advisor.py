"""Optional Gemini explanation layer for v3.4.

Deterministic recommendations are authoritative. Gemini only explains them;
failures/quota exhaustion are swallowed and never block critical alerts.
"""
from __future__ import annotations
import asyncio, hashlib, json, logging, time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote
import httpx
from app.ai_keys import build_gemini_key_pool
from app.config import settings
logger=logging.getLogger(__name__); _BASE="https://generativelanguage.googleapis.com/v1beta/models"
@dataclass
class _Cache:
    expires_at: float
    text: str
_CACHE: dict[str,_Cache]={}; _LOCK=asyncio.Lock(); _KEYS=build_gemini_key_pool()

def recommendation_fingerprint(payload:dict[str,Any])->str:
    material={k:payload.get(k) for k in ("camera","lens","aircraft_category","distance_band","lighting","heat_haze","clarity_band","contrail","framing_band","mode","settings")}
    return hashlib.sha256(json.dumps(material,sort_keys=True,default=str).encode()).hexdigest()[:24]

async def explain(payload:dict[str,Any],*,timeout_s:float=5.0)->str|None:
    if not _KEYS.configured:return None
    key=recommendation_fingerprint(payload); now=time.monotonic()
    async with _LOCK:
        hit=_CACHE.get(key)
        if hit and hit.expires_at>now:
            logger.info("gemini_advisor cache=hit fingerprint=%s",key); return hit.text
    logger.info("gemini_advisor cache=miss fingerprint=%s",key)
    model=(settings.gemini_photo_fallback_model or settings.gemini_photo_model).strip()
    if not model:return None
    prompt=("You are the optional explanation layer for Plane? v3.4 aviation spotting. The deterministic engine already calculated the facts and camera settings. Do not change numeric recommendations. Give 2-4 concise practical sentences. If you mention an alternative, explicitly label it optional.\n\n"+json.dumps(payload,ensure_ascii=False,separators=(",",":"),default=str))
    endpoint=f"{_BASE}/{quote(model,safe='')}:generateContent"; body={"contents":[{"role":"user","parts":[{"text":prompt}]}],"generationConfig":{"thinkingConfig":{"thinkingLevel":"low"},"maxOutputTokens":220}}
    for key_state in _KEYS.candidates():
        _KEYS.record_attempt(key_state)
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_s,connect=min(3.0,timeout_s))) as client:r=await client.post(endpoint,headers={"x-goog-api-key":key_state.key,"Content-Type":"application/json"},json=body)
            r.raise_for_status(); chunks=[]
            for c in r.json().get("candidates") or []:
                for part in (c.get("content") or {}).get("parts") or []:
                    if isinstance(part,dict) and part.get("text"):chunks.append(str(part["text"]))
            text="".join(chunks).strip()
            if not text:return None
            _KEYS.mark_success(key_state)
            async with _LOCK:_CACHE[key]=_Cache(time.monotonic()+3600,text)
            return text
        except httpx.HTTPStatusError as exc:
            status=exc.response.status_code
            if status in (401,403,429):
                _KEYS.mark_http_failure(key_state,status,retry_after=exc.response.headers.get("Retry-After"))
                logger.warning("gemini_advisor credential=%s HTTP=%s rotating",key_state.label,status)
                continue
            logger.warning("gemini_advisor_failed error=%s",type(exc).__name__); return None
        except Exception as exc:
            logger.warning("gemini_advisor_failed error=%s",type(exc).__name__); return None
    return None
