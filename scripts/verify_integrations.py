"""Pre-deploy verification for Telegram and Gemini without exposing credentials."""
from __future__ import annotations

import os
import sys

import httpx


def required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required secret: {name}")
    return value


def verify_telegram() -> None:
    token = required("TELEGRAM_BOT_TOKEN")
    response = httpx.get(f"https://api.telegram.org/bot{token}/getMe", timeout=15)
    if response.status_code != 200:
        raise RuntimeError(f"Telegram token verification failed (HTTP {response.status_code})")
    payload = response.json()
    if not payload.get("ok"):
        raise RuntimeError("Telegram token verification returned ok=false")
    username = (payload.get("result") or {}).get("username") or "unknown"
    print(f"Telegram bot verified: @{username}")


def verify_gemini() -> None:
    key = required("GEMINI_API_KEY")
    payload = {
        "model": "gemini-3.8-flash",
        "store": False,
        "input": "Return a JSON object with ok=true.",
        "generation_config": {"thinking_level": "low", "temperature": 0},
        "response_format": {
            "type": "text",
            "mime_type": "application/json",
            "schema": {
                "type": "object",
                "properties": {"ok": {"type": "boolean"}},
                "required": ["ok"],
                "additionalProperties": False,
            },
        },
    }
    response = httpx.post(
        "https://generativelanguage.googleapis.com/v1beta/interactions",
        headers={"x-goog-api-key": key, "Content-Type": "application/json"},
        json=payload,
        timeout=30,
    )
    if response.status_code != 200:
        raise RuntimeError(f"Gemini 3.8 Flash verification failed (HTTP {response.status_code})")
    data = response.json()
    if not data.get("steps") and not data.get("output_text"):
        raise RuntimeError("Gemini verification returned no model output")
    print("Gemini 3.8 Flash structured-output endpoint verified.")


def main() -> int:
    verify_telegram()
    verify_gemini()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"INTEGRATION VERIFICATION ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
