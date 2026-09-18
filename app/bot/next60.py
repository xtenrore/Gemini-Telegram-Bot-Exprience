"""User-facing Plane Alerts v4.0 next-hour spotting forecast.

The command only reads deterministic Prediction Lab shadow expectations already
stored by the parent-side next-hour auditor. It never creates/cancels alerts and
never calls AI or a paid API.
"""
from __future__ import annotations

import html
from datetime import datetime, timedelta, timezone
from typing import Any

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

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

    # 30–60 minute estimates deliberately show a broad window. Do not imply an
    # exact ETA that the shadow system has not earned yet.
    if horizon_s > 1800 and start is not None and end is not None:
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
    history_days = int(doc.get("historical_days") or 0)
    return (
        f"• <b>{callsign}</b> · {_window_text(now, doc)} · {distance}\n"
        f"  confidence {confidence} · {history_days} historical day{'s' if history_days != 1 else ''}"
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
        "<i>v4.0 Prediction Lab forecast</i>",
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
            "\nNo historical next-hour candidates are available right now. "
            "The v4.0 shadow system needs recent observations of the same flight number before it can forecast it."
        )

    lines.append(
        "\n<i>30–60 min is shadow/history-based and is not a guaranteed alert. "
        "Live CPA/trajectory becomes authoritative as the aircraft gets closer. Missing ADS-B coverage is not scored as a hit or miss.</i>"
    )
    return "\n".join(lines)


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

    now = datetime.now(timezone.utc)
    horizon = now + timedelta(minutes=60)
    cursor = get_db()["prediction_lab_audit"].find(
        {
            "kind": "next_hour_expectation",
            "user_id": user.id,
            "status": "awaiting_outcome",
            "predicted_cpa_at": {"$gte": now, "$lte": horizon},
        },
        {
            "_id": 0,
            "callsign": 1,
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
    await message.reply_text(
        render_next60(now, docs),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


def register_next60_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("next60", cmd_next60))
    app.add_handler(CommandHandler("forecast", cmd_next60))
