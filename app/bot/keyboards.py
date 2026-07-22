"""Inline keyboard builders for the Telegram bot UI."""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.aircraft.categories import CATEGORY_EMOJIS, CATEGORY_ORDER

# ── Callback data prefixes ──────────────────────────────────────────────────
# These are the prefixes used in callback_data to identify which button was
# pressed.  Keep them short (Telegram limits callback_data to 64 bytes).

CB_ACCEPT_TERMS = "terms:accept"
CB_CANCEL = "terms:cancel"
CB_CATEGORY_PREFIX = "cat:"         # followed by category name
CB_TOGGLE_TYPE_PREFIX = "typ:"      # followed by type code
CB_CAT_SALL_PREFIX = "csall:"       # select all in category
CB_CAT_DALL_PREFIX = "cdall:"       # deselect all in category
CB_BACK_MAIN = "cat:back"
CB_DONE = "setup:done"
CB_ADD_CUSTOM = "setup:custom"
CB_REMOVE_CUSTOM_PREFIX = "rcust:"  # remove custom code
CB_SKIP_LOCATION = "loc:skip"
CB_FB_LIKE_PREFIX = "fb:like:"      # followed by notification_id
CB_FB_DISLIKE_PREFIX = "fb:dis:"    # followed by notification_id


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
    selected_cats: set[str] | None = None,
    disabled_types: set[str] | None = None,
    custom_aircraft: list[str] | None = None,
) -> InlineKeyboardMarkup:
    """Build a list of category toggle buttons showing selected type counts."""
    from app.aircraft.categories import AIRCRAFT_CATEGORIES

    if selected_cats is None:
        selected_cats = set()
    if disabled_types is None:
        disabled_types = set()
    if custom_aircraft is None:
        custom_aircraft = []

    rows: list[list[InlineKeyboardButton]] = []
    for name in CATEGORY_ORDER:
        all_types = AIRCRAFT_CATEGORIES.get(name, [])
        total_count = len(all_types)

        if name in selected_cats:
            active_count = sum(1 for t in all_types if t not in disabled_types)
        else:
            active_count = 0

        emoji = CATEGORY_EMOJIS.get(name, "✈️")
        status_icon = "✅" if active_count > 0 else "❌"
        label = f"{status_icon} {emoji} {name} ({active_count}/{total_count})"
        rows.append(
            [InlineKeyboardButton(label, callback_data=f"{CB_CATEGORY_PREFIX}{name}")]
        )

    # Custom types summary button
    custom_count = len(custom_aircraft)
    custom_label = f"✏️ Custom Types ({custom_count})" if custom_count > 0 else "✏️ Add / Manage Custom Types"
    rows.append([InlineKeyboardButton(custom_label, callback_data=CB_ADD_CUSTOM)])
    rows.append([InlineKeyboardButton("✅ Finish Setup", callback_data=CB_DONE)])

    return InlineKeyboardMarkup(rows)


def category_types_sub_keyboard(
    category_name: str,
    disabled_types: set[str] | None = None,
) -> InlineKeyboardMarkup:
    """Build sub-menu for toggling individual aircraft types in a category."""
    from app.aircraft.categories import AIRCRAFT_CATEGORIES

    if disabled_types is None:
        disabled_types = set()

    types = AIRCRAFT_CATEGORIES.get(category_name, [])
    buttons: list[InlineKeyboardButton] = []

    for t in types:
        is_enabled = t not in disabled_types
        icon = "✅" if is_enabled else "❌"
        label = f"{icon} {t}"
        buttons.append(
            InlineKeyboardButton(label, callback_data=f"{CB_TOGGLE_TYPE_PREFIX}{t}")
        )

    # Arrange in 2-column rows for clean text fitting on mobile screens
    rows: list[list[InlineKeyboardButton]] = []
    for i in range(0, len(buttons), 2):
        rows.append(buttons[i : i + 2])

    # Select All / Deselect All control row
    rows.append([
        InlineKeyboardButton("✅ Select All", callback_data=f"{CB_CAT_SALL_PREFIX}{category_name}"),
        InlineKeyboardButton("❌ Deselect All", callback_data=f"{CB_CAT_DALL_PREFIX}{category_name}"),
    ])
    # Done / Back button
    rows.append([
        InlineKeyboardButton("⬅️ Back to Categories", callback_data=CB_BACK_MAIN)
    ])

    return InlineKeyboardMarkup(rows)


def custom_aircraft_keyboard(custom_list: list[str]) -> InlineKeyboardMarkup:
    """Keyboard for custom aircraft management showing deletion buttons."""
    rows: list[list[InlineKeyboardButton]] = []

    if custom_list:
        delete_buttons: list[InlineKeyboardButton] = []
        for code in custom_list:
            delete_buttons.append(
                InlineKeyboardButton(f"🗑️ {code}", callback_data=f"{CB_REMOVE_CUSTOM_PREFIX}{code}")
            )
        # Arrange delete buttons in 2-column rows for clarity
        for i in range(0, len(delete_buttons), 2):
            rows.append(delete_buttons[i : i + 2])

    rows.append([InlineKeyboardButton("⬅️ Back to Categories", callback_data=CB_BACK_MAIN)])
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
