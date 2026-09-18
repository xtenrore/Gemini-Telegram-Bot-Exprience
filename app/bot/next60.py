"""User-facing Plane Alerts v4.1 Next 60 Minutes forecast.

The command opens a Telegram Mini App when a public HTTPS base URL is available.
The web surface is theme-native and scrollable; text remains the safe fallback.
No image generation or paid API is used.
"""
from __future__ import annotations

import html
from datetime import datetime, timedelta, timezone
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, WebAppInfo
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

from app.config import settings
from app.database import get_db, users_col

MAX_ROWS = 30


def _aware(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _minutes_from(now: datetime, value: datetime | None) -> int | None:
    if value is None:
        return None
    return max(0, int(round((value - now).total_seconds() / 60.0)))


def _bucket(horizon_minutes: float) -> str:
    if horizon_minutes <= 15:
        return "0–15 min"
    if horizon_minutes <= 30:
        return "15–30 min"
    return "30–60 min"


def _window_text(now: datetime, doc: dict[str, Any]) -> str:
    start = _minutes_from(now, _aware(doc.get("window_start")))
    end = _minutes_from(now, _aware(doc.get("window_end")))
    cpa = _minutes_from(now, _aware(doc.get("predicted_cpa_at")))
    horizon_s = float(doc.get("prediction_horizon_s") or 0.0)
    source = str(doc.get("source") or "history")

    if source != "live" and horizon_s > 1800 and start is not None and end is not None:
        return f"window in ~{start}–{end} min"
    if cpa is not None:
        return f"closest pass in ~{cpa} min"
    if start is not None and end is not None:
        return f"window in ~{start}–{end} min"
    return "timing window unavailable"


def _row(now: datetime, doc: dict[str, Any]) -> str:
    callsign = html.escape(str(doc.get("callsign") or "Unknown"))
    confidence = html.escape(str(doc.get("confidence") or "Low"))
    closest = doc.get("predicted_closest_km")
    distance = "? km"
    try:
        distance = f"~{float(closest):.1f} km"
    except (TypeError, ValueError):
        pass

    if doc.get("source") == "live":
        stage = html.escape(str(doc.get("stage") or "live"))
        evidence = f"live trajectory · {stage}"
    else:
        history_days = int(doc.get("historical_days") or 0)
        evidence = f"history shadow · {history_days} day{'s' if history_days != 1 else ''}"

    return (
        f"• <b>{callsign}</b> · {_window_text(now, doc)} · {distance}\n"
        f"  confidence {confidence} · {evidence}"
    )


def render_next60(now: datetime, docs: list[dict[str, Any]]) -> str:
    buckets: dict[str, list[dict[str, Any]]] = {
        "0–15 min": [],
        "15–30 min": [],
        "30–60 min": [],
    }
    for doc in docs:
        cpa = _aware(doc.get("predicted_cpa_at"))
        if cpa is None:
            continue
        horizon = (cpa - now).total_seconds() / 60.0
        if horizon < 0 or horizon > 60:
            continue
        buckets[_bucket(horizon)].append(doc)

    lines = [
        "✈️ <b>Plane Alerts · Next 60 Minutes</b>",
        "<i>v4.1 live + Prediction Lab forecast</i>",
    ]
    any_rows = False
    for label in ("0–15 min", "15–30 min", "30–60 min"):
        rows = buckets[label]
        lines.append(f"\n<b>{label}</b>")
        if not rows:
            lines.append("No current candidates.")
            continue
        any_rows = True
        rows.sort(key=lambda d: _aware(d.get("predicted_cpa_at")) or now)
        lines.extend(_row(now, row) for row in rows[:10])

    if not any_rows:
        lines.append(
            "\nNo next-hour candidates are available right now. New flight numbers need observed route history before the longer-range shadow system can forecast them."
        )

    lines.append(
        "\n<i>Live trajectory/CPA is preferred when available. 30–60 min history entries are shadow estimates, not guaranteed alerts. Missing ADS-B coverage is not scored as a hit or miss.</i>"
    )
    return "\n".join(lines)


def _identity(doc: dict[str, Any]) -> str:
    return str(doc.get("callsign") or doc.get("aircraft_icao24") or "").strip().upper()


async def _history_docs(user_id: int, now: datetime) -> list[dict[str, Any]]:
    horizon = now + timedelta(minutes=60)
    cursor = get_db()["prediction_lab_audit"].find(
        {
            "kind": "next_hour_expectation",
            "user_id": user_id,
            "status": "awaiting_outcome",
            "predicted_cpa_at": {"$gte": now, "$lte": horizon},
        },
        {
            "_id": 0,
            "callsign": 1,
            "aircraft_type": 1,
            "predicted_cpa_at": 1,
            "window_start": 1,
            "window_end": 1,
            "prediction_horizon_s": 1,
            "predicted_closest_km": 1,
            "historical_days": 1,
            "confidence": 1,
        },
    ).sort("predicted_cpa_at", 1).limit(MAX_ROWS)
    docs = [doc async for doc in cursor]
    for doc in docs:
        doc["source"] = "history"
    return docs


async def _live_docs(user_id: int, now: datetime) -> list[dict[str, Any]]:
    cursor = get_db()["approach_states"].find(
        {
            "user_id": user_id,
            "active": True,
            "time_to_cpa_s": {"$gte": 0, "$lte": 3600},
        },
        {
            "_id": 0,
            "aircraft_icao24": 1,
            "route_callsign": 1,
            "aircraft_type": 1,
            "projected_closest_km": 1,
            "time_to_cpa_s": 1,
            "confidence": 1,
            "stage": 1,
        },
    ).limit(MAX_ROWS)
    docs: list[dict[str, Any]] = []
    async for state in cursor:
        try:
            eta_s = float(state.get("time_to_cpa_s"))
        except (TypeError, ValueError):
            continue
        if eta_s < 0 or eta_s > 3600:
            continue
        callsign = str(state.get("route_callsign") or state.get("aircraft_icao24") or "Unknown").upper()
        docs.append({
            "callsign": callsign,
            "aircraft_icao24": state.get("aircraft_icao24"),
            "aircraft_type": state.get("aircraft_type"),
            "predicted_cpa_at": now + timedelta(seconds=eta_s),
            "prediction_horizon_s": eta_s,
            "predicted_closest_km": state.get("projected_closest_km"),
            "confidence": state.get("confidence") or "Low",
            "stage": state.get("stage") or "live",
            "source": "live",
        })
    return docs


async def build_next60_docs(user_id: int, now: datetime) -> list[dict[str, Any]]:
    """Build one merged forecast, preferring live deterministic geometry."""
    history = await _history_docs(user_id, now)
    live = await _live_docs(user_id, now)

    merged: dict[str, dict[str, Any]] = {}
    anonymous = 0
    for doc in history:
        key = _identity(doc)
        if not key:
            anonymous += 1
            key = f"history-{anonymous}"
        merged[key] = doc
    for doc in live:
        key = _identity(doc)
        if not key:
            anonymous += 1
            key = f"live-{anonymous}"
        merged[key] = doc

    docs = list(merged.values())
    docs.sort(key=lambda d: _aware(d.get("predicted_cpa_at")) or now)
    return docs[:MAX_ROWS]


def _next60_web_app_url() -> str | None:
    base = settings.webhook_url.strip().rstrip("/")
    if not base.lower().startswith("https://"):
        return None
    return f"{base}/next60-ui"


async def cmd_next60(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    user = update.effective_user
    message = update.message
    if user is None or message is None:
        return

    user_doc = await users_col().find_one({"user_id": user.id}, {"setup_complete": 1})
    if not user_doc or not user_doc.get("setup_complete"):
        await message.reply_text("Finish /start setup first so Plane Alerts knows which location to forecast for.")
        return

    web_app_url = _next60_web_app_url()
    if web_app_url:
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(
            "Open Next 60",
            web_app=WebAppInfo(url=web_app_url),
        )]])
        await message.reply_text(
            "✈️ <b>Next 60 Minutes</b>\nLive trajectory + Prediction Lab forecast.",
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
            disable_web_page_preview=True,
        )
        return

    now = datetime.now(timezone.utc)
    docs = await build_next60_docs(user.id, now)
    await message.reply_text(
        render_next60(now, docs),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


def register_next60_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("next60", cmd_next60))
    app.add_handler(CommandHandler("forecast", cmd_next60))
