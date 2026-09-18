from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from app.bot.next60_web import NEXT60_HTML, serialize_next60, validate_telegram_init_data


def _signed_init_data(*, token: str, user_id: int, auth_date: int) -> str:
    values = {
        "auth_date": str(auth_date),
        "query_id": "AAE-test",
        "user": json.dumps({"id": user_id, "first_name": "Test"}, separators=(",", ":")),
    }
    check = "\n".join(f"{key}={values[key]}" for key in sorted(values))
    secret = hmac.new(b"WebAppData", token.encode("utf-8"), hashlib.sha256).digest()
    values["hash"] = hmac.new(secret, check.encode("utf-8"), hashlib.sha256).hexdigest()
    return urlencode(values)


def test_telegram_init_data_signature_and_age_are_verified():
    token = "123456:TEST-TOKEN"
    now = 1_800_000_000
    signed = _signed_init_data(token=token, user_id=42, auth_date=now - 30)
    assert validate_telegram_init_data(signed, token, now=now) == 42

    assert validate_telegram_init_data(signed.replace("42", "43", 1), token, now=now) is None
    stale = _signed_init_data(token=token, user_id=42, auth_date=now - 4000)
    assert validate_telegram_init_data(stale, token, now=now) is None


def test_next60_serializer_preserves_horizon_and_sources():
    now = datetime(2026, 9, 18, 18, 0, tzinfo=timezone.utc)
    payload = serialize_next60(now, [
        {
            "callsign": "THY7",
            "aircraft_type": "A359",
            "predicted_cpa_at": now + timedelta(minutes=8),
            "predicted_closest_km": 4.25,
            "confidence": "High",
            "source": "live",
            "stage": "camera_ready",
        },
        {
            "callsign": "BAW123",
            "predicted_cpa_at": now + timedelta(minutes=44),
            "predicted_closest_km": 10.94,
            "confidence": "Low",
            "source": "history",
            "historical_days": 3,
        },
    ])
    assert payload["count"] == 2
    assert payload["rows"][0]["bucket"] == "0–15"
    assert payload["rows"][0]["aircraft_type"] == "A359"
    assert payload["rows"][0]["source"] == "live"
    assert payload["rows"][1]["bucket"] == "30–60"
    assert payload["rows"][1]["source"] == "shadow"
    assert payload["rows"][1]["history_days"] == 3


def test_next60_html_uses_telegram_theme_and_roomier_rows():
    assert "--tg-theme-bg-color" in NEXT60_HTML
    assert "--tg-theme-text-color" in NEXT60_HTML
    assert "#101316" not in NEXT60_HTML
    assert "min-height:124px" in NEXT60_HTML
    assert "gap:18px" in NEXT60_HTML
    assert "tg.expand()" in NEXT60_HTML
    assert "fetch('/api/next60'" in NEXT60_HTML
