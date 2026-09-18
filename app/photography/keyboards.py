"""Inline keyboard additions for v3.2 photography actions."""
from __future__ import annotations

from urllib.parse import quote

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from app.bot.keyboards import CB_FB_DISLIKE_PREFIX, CB_FB_LIKE_PREFIX

PHOTO_CALLBACK_PREFIX = "photo:"


def notification_actions_keyboard(notification_id: str, icao24: str | None = None) -> InlineKeyboardMarkup:
    """Aircraft alert actions: ADSB.fi, camera settings, and feedback."""
    rows: list[list[InlineKeyboardButton]] = []
    if icao24:
        rows.append([
            InlineKeyboardButton(
                "Open in ADSB.fi",
                url=f"https://globe.adsb.fi/?icao={quote(str(icao24).lower(), safe='')}",
            )
        ])
    rows.extend([
        [InlineKeyboardButton("📷 Best camera settings", callback_data=f"{PHOTO_CALLBACK_PREFIX}{notification_id}")],
        [
            InlineKeyboardButton("👍 Helpful", callback_data=f"{CB_FB_LIKE_PREFIX}{notification_id}"),
            InlineKeyboardButton("👎 Not Helpful / Wrong", callback_data=f"{CB_FB_DISLIKE_PREFIX}{notification_id}"),
        ],
    ])
    return InlineKeyboardMarkup(rows)
