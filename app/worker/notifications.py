"""Notification sending with rate limiting and error handling.

Handles sending Telegram messages to users and gracefully handles
blocked-bot errors by marking users inactive.
"""

from __future__ import annotations

import asyncio
import logging

from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import Forbidden, TelegramError

from app.aircraft.models import NormalizedAircraft
from app.bot.messages import aircraft_alert_message
from app.config import settings
from app.database import users_col

logger = logging.getLogger(__name__)

# Simple rate limiter: max messages per second to avoid Telegram limits.
# Telegram allows ~30 messages/s globally, we stay well under.
_send_semaphore = asyncio.Semaphore(20)
_MIN_SEND_INTERVAL = 0.05  # 50ms between sends


async def send_aircraft_notification(
    user_id: int,
    aircraft: NormalizedAircraft,
    distance_km: float,
) -> bool:
    """Send an aircraft alert to a user.

    Returns ``True`` if the message was sent successfully.
    """
    msg = aircraft_alert_message(
        aircraft_type=aircraft.display_type,
        callsign=aircraft.callsign,
        distance_km=distance_km,
        altitude_m=aircraft.altitude,
        velocity_ms=aircraft.velocity,
        heading=aircraft.heading,
        icao24=aircraft.icao24,
        origin_country=aircraft.origin_country,
    )

    return await _send_message(user_id, msg)


async def _send_message(user_id: int, text: str) -> bool:
    """Send a Telegram message with rate limiting and error handling."""
    async with _send_semaphore:
        try:
            bot = Bot(token=settings.telegram_bot_token)
            await bot.send_message(
                chat_id=user_id,
                text=text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
            logger.info("Notification sent to user %d", user_id)
            await asyncio.sleep(_MIN_SEND_INTERVAL)
            return True

        except Forbidden:
            # User blocked the bot — mark inactive so we stop trying
            logger.warning(
                "User %d has blocked the bot — marking setup_complete=False", user_id
            )
            await users_col().update_one(
                {"user_id": user_id},
                {"$set": {"setup_complete": False}},
            )
            return False

        except TelegramError as exc:
            logger.error("Failed to send notification to user %d: %s", user_id, exc)
            return False

        except Exception:
            logger.exception("Unexpected error sending notification to user %d", user_id)
            return False


async def send_admin_alert(text: str) -> None:
    """Send an alert to the configured admin Telegram user (if set)."""
    if not settings.admin_telegram_id:
        logger.warning("Admin alert (no admin ID configured): %s", text)
        return

    try:
        bot = Bot(token=settings.telegram_bot_token)
        await bot.send_message(
            chat_id=settings.admin_telegram_id,
            text=f"🔔 <b>Admin Alert</b>\n\n{text}",
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        logger.exception("Failed to send admin alert")
