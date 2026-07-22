"""Inline keyboard builders for the Telegram bot UI."""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.aircraft.categories import CATEGORY_EMOJIS, CATEGORY_ORDER

# ── Callback data prefixes ──────────────────────────────────────────────────
# These are the prefixes used in callback_data to identify which button was
# pressed.  Keep them short (Telegram limits callback_data to 64 bytes).

CB_ACCEPT_TERMS = "terms:accept"
CB_CANCEL = "terms:cancel"
CB_CATEGORY_PREFIX = "cat:"      # followed by category name
CB_DONE = "setup:done"
CB_ADD_CUSTOM = "setup:custom"
CB_SKIP_LOCATION = "loc:skip"
CB_FB_LIKE_PREFIX = "fb:like:"   # followed by notification_id
CB_FB_DISLIKE_PREFIX = "fb:dis:" # followed by notification_id


# ── Keyboards ────────────────────────────────────────────────────────────────

def terms_keyboard() -> InlineKeyboardMarkup:
    """Accept / Cancel disclaimer keyboard."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Accept & Continue", callback_data=CB_ACCEPT_TERMS),
                InlineKeyboardButton("❌ Cancel", callback_data=CB_CANCEL),
            ]
        ]
    )


def aircraft_categories_keyboard(
    selected: set[str] | None = None,
) -> InlineKeyboardMarkup:
    """Build a grid of category toggle buttons.

    Selected categories show a ✓ checkmark.  Laid out in 2-column rows.
    """
    if selected is None:
        selected = set()

    buttons: list[InlineKeyboardButton] = []
    for name in CATEGORY_ORDER:
        emoji = CATEGORY_EMOJIS.get(name, "✈️")
        check = " ✓" if name in selected else ""
        label = f"{emoji} {name}{check}"
        buttons.append(
            InlineKeyboardButton(label, callback_data=f"{CB_CATEGORY_PREFIX}{name}")
        )

    # Arrange in 2-column rows
    rows: list[list[InlineKeyboardButton]] = []
    for i in range(0, len(buttons), 2):
        rows.append(buttons[i : i + 2])

    # Action buttons at the bottom
    rows.append(
        [
            InlineKeyboardButton("✏️ Add Custom Type", callback_data=CB_ADD_CUSTOM),
        ]
    )
    rows.append(
        [
            InlineKeyboardButton("✅ Done", callback_data=CB_DONE),
        ]
    )

    return InlineKeyboardMarkup(rows)


def skip_location_keyboard() -> InlineKeyboardMarkup:
    """Optional skip button for the location step."""
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("⏭️ Skip for now", callback_data=CB_SKIP_LOCATION)]
        ]
    )


def notification_feedback_keyboard(notification_id: str) -> InlineKeyboardMarkup:
    """Like / Dislike feedback buttons for aircraft notification alerts."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "👍 Helpful",
                    callback_data=f"{CB_FB_LIKE_PREFIX}{notification_id}",
                ),
                InlineKeyboardButton(
                    "👎 Not Helpful / Wrong",
                    callback_data=f"{CB_FB_DISLIKE_PREFIX}{notification_id}",
                ),
            ]
        ]
    )
