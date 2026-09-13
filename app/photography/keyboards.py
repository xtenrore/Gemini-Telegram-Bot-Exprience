"""Inline keyboard additions for v3.2 photography actions."""
from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from app.bot.keyboards import CB_FB_DISLIKE_PREFIX, CB_FB_LIKE_PREFIX

PHOTO_CALLBACK_PREFIX = "photo:"


def notification_actions_keyboard(notification_id: str) -> InlineKeyboardMarkup:
    """Aircraft alert actions: Gemini camera settings plus feedback."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📷 Best camera settings", callback_data=f"{PHOTO_CALLBACK_PREFIX}{notification_id}")],
        [
            InlineKeyboardButton("👍 Helpful", callback_data=f"{CB_FB_LIKE_PREFIX}{notification_id}"),
            InlineKeyboardButton("👎 Not Helpful / Wrong", callback_data=f"{CB_FB_DISLIKE_PREFIX}{notification_id}"),
        ],
    ])
