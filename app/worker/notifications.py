"""Telegram notification sending with rate limiting and v3.2 photo actions."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from html import escape
from urllib.parse import quote

from telegram import Bot, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.error import Forbidden, TelegramError

from app.aircraft.models import NormalizedAircraft
from app.bot.messages import aircraft_alert_message
from app.config import settings
from app.database import get_db, users_col
from app.photography.keyboards import notification_actions_keyboard

logger = logging.getLogger(__name__)
_send_semaphore = asyncio.Semaphore(20)
_MIN_SEND_INTERVAL = 0.05


def _safe_provider_text(value: str) -> str:
    return escape(value or "", quote=True)


async def _record_photo_snapshot(
    user_id: int,
    aircraft: NormalizedAircraft,
    distance_km: float,
    notification_id: str,
    eta_seconds: float | None,
) -> None:
    """Persist the exact alert context before Telegram can expose its button.

    This removes the race where a user taps the photography button before the
    monitor has finished writing notification history. The snapshot is only a
    fast starting point; the photography service still refreshes the target
    aircraft from live ADS-B when possible.
    """
    if not notification_id:
        return
    now = datetime.now(timezone.utc)
    await get_db()["photo_alert_snapshots"].update_one(
        {"_id": notification_id, "user_id": user_id},
        {
            "$set": {
                "user_id": user_id,
                "aircraft_icao24": aircraft.icao24 or "",
                "aircraft_type": aircraft.aircraft_type or aircraft.display_type or "",
                "callsign": aircraft.callsign or "",
                "distance_km": float(distance_km),
                "altitude_m": aircraft.altitude,
                "speed_ms": aircraft.velocity,
                "heading_deg": aircraft.heading,
                "latitude": aircraft.latitude,
                "longitude": aircraft.longitude,
                "eta_seconds": eta_seconds,
                "captured_at": now,
                "expires_at": now + timedelta(hours=6),
            }
        },
        upsert=True,
    )


async def send_aircraft_notification(
    user_id: int,
    aircraft: NormalizedAircraft,
    distance_km: float,
    notification_id: str = "",
    eta_seconds: float | None = None,
) -> bool:
    msg = aircraft_alert_message(
        aircraft_type=_safe_provider_text(aircraft.display_type),
        callsign=_safe_provider_text(aircraft.callsign),
        distance_km=distance_km,
        altitude_m=aircraft.altitude,
        velocity_ms=aircraft.velocity,
        heading=aircraft.heading,
        icao24=quote(aircraft.icao24 or "", safe=""),
        origin_country=_safe_provider_text(aircraft.origin_country),
        eta_seconds=eta_seconds,
    )
    reply_markup = notification_actions_keyboard(notification_id) if notification_id else None

    if notification_id:
        try:
            await _record_photo_snapshot(user_id, aircraft, distance_km, notification_id, eta_seconds)
        except Exception:
            # Notification delivery must not be blocked by a photography-cache write.
            logger.exception("Could not persist photo snapshot for notification %s", notification_id)

    return await _send_message(user_id, msg, reply_markup=reply_markup)


_bot_instance: Bot | None = None


def _get_bot() -> Bot:
    global _bot_instance
    if _bot_instance is None or _bot_instance.token != settings.telegram_bot_token:
        _bot_instance = Bot(token=settings.telegram_bot_token)
    return _bot_instance


async def _send_message(user_id: int, text: str, reply_markup: InlineKeyboardMarkup | None = None) -> bool:
    async with _send_semaphore:
        try:
            bot = _get_bot()
            await bot.send_message(
                chat_id=user_id,
                text=text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
                reply_markup=reply_markup,
            )
            logger.info("Notification sent to user %d", user_id)
            await asyncio.sleep(_MIN_SEND_INTERVAL)
            return True
        except Forbidden:
            logger.warning("User %d blocked the bot; marking setup incomplete", user_id)
            await users_col().update_one({"user_id": user_id}, {"$set": {"setup_complete": False}})
            return False
        except TelegramError as exc:
            logger.error("Failed to send Telegram notification to user %d: %s", user_id, exc)
            return False
        except Exception:
            logger.exception("Unexpected error sending Telegram notification to user %d", user_id)
            return False


async def send_admin_alert(text: str) -> None:
    if not settings.admin_telegram_id:
        logger.warning("Admin alert (no admin Telegram ID): %s", text)
        return
    try:
        await _get_bot().send_message(
            chat_id=settings.admin_telegram_id,
            text=f"🔔 <b>Admin Alert</b>\n\n{escape(text, quote=True)}",
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        logger.exception("Failed to send admin alert")
