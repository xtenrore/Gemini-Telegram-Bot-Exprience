"""Telegram bot command and message handlers.

This module wires up every user interaction:
  • /start, /setup, /status, /help, /location, /preferences commands
  • Callback queries (inline keyboard button presses)
  • Location messages
  • Free-text messages (custom ICAO codes)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.aircraft.categories import (
    CATEGORY_ORDER,
    get_all_types_for_categories,
    validate_icao_code,
)
from app.aircraft.learner import provider_learner
from app.bot.feedback import handle_feedback_callback
from app.bot.keyboards import (
    CB_ACCEPT_TERMS,
    CB_ADD_CUSTOM,
    CB_BACK_MAIN,
    CB_CANCEL,
    CB_CAT_DALL_PREFIX,
    CB_CAT_SALL_PREFIX,
    CB_CATEGORY_PREFIX,
    CB_DONE,
    CB_FB_DISLIKE_PREFIX,
    CB_FB_LIKE_PREFIX,
    CB_REMOVE_CUSTOM_PREFIX,
    CB_SKIP_LOCATION,
    CB_TOGGLE_TYPE_PREFIX,
    aircraft_categories_keyboard,
    category_types_sub_keyboard,
    custom_aircraft_keyboard,
    skip_location_keyboard,
    terms_keyboard,
)
from app.bot.messages import (
    AIRCRAFT_SELECTION_PROMPT,
    CANCEL_MESSAGE,
    CUSTOM_AIRCRAFT_ADDED,
    CUSTOM_AIRCRAFT_PROMPT,
    HELP_MESSAGE,
    INVALID_ICAO_CODE,
    INVALID_RADIUS,
    LOCATION_PROMPT,
    LOCATION_SAVED,
    NO_CATEGORIES_SELECTED,
    RADIUS_PROMPT,
    WELCOME_MESSAGE,
    setup_complete_message,
    status_message,
)
from app.bot.states import (
    UserState,
    clear_user_state,
    get_temp_data,
    get_user_state,
    set_user_state,
    update_temp_data,
)
from app.config import settings
from app.database import locations_col, preferences_col, users_col
from app.worker.geo import compute_geohash

logger = logging.getLogger(__name__)


# ── Handler registration ────────────────────────────────────────────────────

def register_handlers(app: Application) -> None:
    """Register all handlers on the ``python-telegram-bot`` Application."""
    # Commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("setup", cmd_setup))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("location", cmd_location))
    app.add_handler(CommandHandler("preferences", cmd_preferences))
    app.add_handler(CommandHandler("cancel", cmd_cancel))

    # Callback queries (inline keyboard buttons)
    app.add_handler(CallbackQueryHandler(cb_handler))

    # Location messages
    app.add_handler(MessageHandler(filters.LOCATION, on_location))

    # Free-text messages (custom ICAO codes, etc.)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    logger.info("All bot handlers registered.")


# ═══════════════════════════════════════════════════════════════════════════
# COMMANDS
# ═══════════════════════════════════════════════════════════════════════════

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/start — Welcome + disclaimer."""
    user = update.effective_user
    if user is None or update.message is None:
        return

    # Upsert user record
    await users_col().update_one(
        {"user_id": user.id},
        {
            "$set": {
                "username": user.username or "",
                "first_name": user.first_name or "",
                "last_active": datetime.now(timezone.utc),
            },
            "$setOnInsert": {
                "user_id": user.id,
                "setup_complete": False,
                "terms_accepted": False,
                "created_at": datetime.now(timezone.utc),
            },
        },
        upsert=True,
    )

    # Show welcome + disclaimer
    await set_user_state(user.id, UserState.WAITING_TERMS)
    await update.message.reply_text(
        WELCOME_MESSAGE,
        parse_mode=ParseMode.HTML,
        reply_markup=terms_keyboard(),
    )


async def cmd_setup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/setup — Re-run full setup (reset preferences)."""
    user = update.effective_user
    if user is None or update.message is None:
        return

    # Clear existing preferences
    await preferences_col().delete_one({"user_id": user.id})
    await locations_col().delete_one({"user_id": user.id})
    await users_col().update_one(
        {"user_id": user.id},
        {"$set": {"setup_complete": False, "terms_accepted": False}},
    )

    # Restart setup
    await cmd_start(update, context)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/status — Show current monitoring configuration."""
    user = update.effective_user
    if user is None or update.message is None:
        return

    user_doc = await users_col().find_one({"user_id": user.id})
    prefs_doc = await preferences_col().find_one({"user_id": user.id})
    loc_doc = await locations_col().find_one({"user_id": user.id})

    is_setup = bool(user_doc and user_doc.get("setup_complete"))
    cats = prefs_doc.get("selected_categories", []) if prefs_doc else []
    custom = prefs_doc.get("custom_aircraft", []) if prefs_doc else []
    lat = loc_doc.get("latitude") if loc_doc else None
    lon = loc_doc.get("longitude") if loc_doc else None

    radius = loc_doc.get("radius_km", settings.default_radius_km) if loc_doc else settings.default_radius_km

    msg = status_message(
        selected_categories=cats,
        custom_aircraft=custom,
        lat=lat,
        lon=lon,
        radius_km=radius,
        setup_complete=is_setup,
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/help — Show all commands."""
    if update.message is None:
        return
    await update.message.reply_text(HELP_MESSAGE, parse_mode=ParseMode.HTML)


async def cmd_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/location — Update location only (shortcut)."""
    user = update.effective_user
    if user is None or update.message is None:
        return

    await set_user_state(user.id, UserState.WAITING_LOCATION)
    await update.message.reply_text(
        LOCATION_PROMPT,
        parse_mode=ParseMode.HTML,
        reply_markup=skip_location_keyboard(),
    )


async def cmd_preferences(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/preferences — Update aircraft type selection only (shortcut)."""
    user = update.effective_user
    if user is None or update.message is None:
        return

    # Load existing selections as starting point
    prefs_doc = await preferences_col().find_one({"user_id": user.id})
    selected = set(prefs_doc.get("selected_categories", [])) if prefs_doc else set()
    disabled = set(prefs_doc.get("disabled_types", [])) if prefs_doc else set()
    custom = prefs_doc.get("custom_aircraft", []) if prefs_doc else []

    await set_user_state(
        user.id,
        UserState.WAITING_AIRCRAFT_SELECTION,
        temp_data={
            "selected_categories": list(selected),
            "disabled_types": list(disabled),
            "custom_aircraft": custom,
        },
    )
    await update.message.reply_text(
        AIRCRAFT_SELECTION_PROMPT,
        parse_mode=ParseMode.HTML,
        reply_markup=aircraft_categories_keyboard(selected, disabled, custom),
    )


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/cancel — Cancel current operation."""
    user = update.effective_user
    if user is None or update.message is None:
        return
    await clear_user_state(user.id)
    await update.message.reply_text(CANCEL_MESSAGE, parse_mode=ParseMode.HTML)


# ═══════════════════════════════════════════════════════════════════════════
# CALLBACK QUERIES (inline button presses)
# ═══════════════════════════════════════════════════════════════════════════

async def cb_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route all callback queries to the appropriate handler."""
    query = update.callback_query
    if query is None or query.data is None:
        return

    await query.answer()  # Acknowledge to Telegram immediately
    data = query.data
    user = update.effective_user
    if user is None:
        return

    if data == CB_ACCEPT_TERMS:
        await _on_accept_terms(update, user.id)
    elif data == CB_CANCEL:
        await _on_cancel(update, user.id)
    elif data == CB_BACK_MAIN:
        await _on_back_to_main_categories(update, user.id)
    elif data.startswith(CB_CATEGORY_PREFIX):
        category = data[len(CB_CATEGORY_PREFIX):]
        await _on_open_category_sub(update, user.id, category)
    elif data.startswith(CB_TOGGLE_TYPE_PREFIX):
        type_code = data[len(CB_TOGGLE_TYPE_PREFIX):]
        await _on_toggle_type_code(update, user.id, type_code)
    elif data.startswith(CB_CAT_SALL_PREFIX):
        category = data[len(CB_CAT_SALL_PREFIX):]
        await _on_select_all_in_cat(update, user.id, category)
    elif data.startswith(CB_CAT_DALL_PREFIX):
        category = data[len(CB_CAT_DALL_PREFIX):]
        await _on_deselect_all_in_cat(update, user.id, category)
    elif data.startswith(CB_REMOVE_CUSTOM_PREFIX):
        code = data[len(CB_REMOVE_CUSTOM_PREFIX):]
        await _on_remove_custom_code(update, user.id, code)
    elif data == CB_DONE:
        await _on_setup_done(update, user.id)
    elif data == CB_ADD_CUSTOM:
        await _on_add_custom(update, user.id)
    elif data == CB_SKIP_LOCATION:
        await _on_skip_location(update, user.id)
    elif data.startswith(CB_FB_LIKE_PREFIX) or data.startswith(CB_FB_DISLIKE_PREFIX):
        await handle_feedback_callback(update)


async def _on_accept_terms(update: Update, user_id: int) -> None:
    """User accepted the disclaimer."""
    await users_col().update_one(
        {"user_id": user_id},
        {"$set": {"terms_accepted": True}},
    )
    await set_user_state(user_id, UserState.WAITING_LOCATION)

    query = update.callback_query
    if query and query.message:
        await query.message.edit_text(
            "✅ Disclaimer accepted.\n\n" + LOCATION_PROMPT,
            parse_mode=ParseMode.HTML,
            reply_markup=skip_location_keyboard(),
        )


async def _on_cancel(update: Update, user_id: int) -> None:
    """User cancelled setup."""
    await clear_user_state(user_id)
    query = update.callback_query
    if query and query.message:
        await query.message.edit_text(CANCEL_MESSAGE, parse_mode=ParseMode.HTML)


async def _on_open_category_sub(update: Update, user_id: int, category: str) -> None:
    """Open sub-menu for category aircraft types."""
    from app.aircraft.categories import AIRCRAFT_CATEGORIES, CATEGORY_ORDER

    if category not in CATEGORY_ORDER:
        return

    temp = await get_temp_data(user_id)
    selected_cats: list[str] = temp.get("selected_categories", [])
    disabled_types: list[str] = temp.get("disabled_types", [])

    # If category is not selected yet, select it (enables all types by default)
    if category not in selected_cats:
        selected_cats.append(category)
        all_types = set(AIRCRAFT_CATEGORIES.get(category, []))
        disabled_types = [t for t in disabled_types if t not in all_types]

    await update_temp_data(
        user_id,
        {
            "selected_categories": selected_cats,
            "disabled_types": disabled_types,
            "current_category": category,
        },
    )

    query = update.callback_query
    if query and query.message:
        await query.message.edit_text(
            f"✈️ <b>{category} Aircraft Selection</b>\n\n"
            "Tap any type code to enable (✅) or disable (❌).\n"
            "When finished, tap <b>⬅️ Back to Categories</b>.",
            parse_mode=ParseMode.HTML,
            reply_markup=category_types_sub_keyboard(category, set(disabled_types)),
        )


async def _on_toggle_type_code(update: Update, user_id: int, type_code: str) -> None:
    """Toggle individual aircraft type in category sub-menu."""
    temp = await get_temp_data(user_id)
    category = temp.get("current_category", "")
    disabled_types: list[str] = temp.get("disabled_types", [])

    if type_code in disabled_types:
        disabled_types.remove(type_code)
    else:
        disabled_types.append(type_code)

    await update_temp_data(user_id, {"disabled_types": disabled_types})

    query = update.callback_query
    if query and query.message and category:
        await query.message.edit_reply_markup(
            reply_markup=category_types_sub_keyboard(category, set(disabled_types)),
        )


async def _on_select_all_in_cat(update: Update, user_id: int, category: str) -> None:
    """Enable all types in category."""
    from app.aircraft.categories import AIRCRAFT_CATEGORIES

    temp = await get_temp_data(user_id)
    selected_cats: list[str] = temp.get("selected_categories", [])
    disabled_types: list[str] = temp.get("disabled_types", [])

    if category not in selected_cats:
        selected_cats.append(category)

    all_cat_types = set(AIRCRAFT_CATEGORIES.get(category, []))
    disabled_types = [t for t in disabled_types if t not in all_cat_types]

    await update_temp_data(
        user_id,
        {"selected_categories": selected_cats, "disabled_types": disabled_types},
    )

    query = update.callback_query
    if query and query.message:
        await query.message.edit_reply_markup(
            reply_markup=category_types_sub_keyboard(category, set(disabled_types)),
        )


async def _on_deselect_all_in_cat(update: Update, user_id: int, category: str) -> None:
    """Disable all types in category."""
    from app.aircraft.categories import AIRCRAFT_CATEGORIES

    temp = await get_temp_data(user_id)
    selected_cats: list[str] = temp.get("selected_categories", [])
    disabled_types: list[str] = temp.get("disabled_types", [])

    if category in selected_cats:
        selected_cats.remove(category)

    all_cat_types = AIRCRAFT_CATEGORIES.get(category, [])
    for t in all_cat_types:
        if t not in disabled_types:
            disabled_types.append(t)

    await update_temp_data(
        user_id,
        {"selected_categories": selected_cats, "disabled_types": disabled_types},
    )

    query = update.callback_query
    if query and query.message:
        await query.message.edit_reply_markup(
            reply_markup=category_types_sub_keyboard(category, set(disabled_types)),
        )


async def _on_back_to_main_categories(update: Update, user_id: int) -> None:
    """Return to main category selection keyboard."""
    temp = await get_temp_data(user_id)
    selected_cats = set(temp.get("selected_categories", []))
    disabled_types = set(temp.get("disabled_types", []))
    custom = temp.get("custom_aircraft", [])

    query = update.callback_query
    if query and query.message:
        await query.message.edit_text(
            AIRCRAFT_SELECTION_PROMPT,
            parse_mode=ParseMode.HTML,
            reply_markup=aircraft_categories_keyboard(
                selected_cats, disabled_types, custom
            ),
        )


async def _on_remove_custom_code(update: Update, user_id: int, code: str) -> None:
    """Remove a custom ICAO code from custom_aircraft list."""
    temp = await get_temp_data(user_id)
    custom: list[str] = temp.get("custom_aircraft", [])

    if code in custom:
        custom.remove(code)
        await update_temp_data(user_id, {"custom_aircraft": custom})

    query = update.callback_query
    if query and query.message:
        await query.message.edit_reply_markup(
            reply_markup=custom_aircraft_keyboard(custom)
        )


async def _on_setup_done(update: Update, user_id: int) -> None:
    """User finished aircraft selection — finalise setup."""
    temp = await get_temp_data(user_id)
    selected_cats: list[str] = temp.get("selected_categories", [])
    disabled_types: list[str] = temp.get("disabled_types", [])
    custom: list[str] = temp.get("custom_aircraft", [])

    if not selected_cats and not custom:
        query = update.callback_query
        if query:
            await query.answer(NO_CATEGORIES_SELECTED, show_alert=True)
        return

    # Persist preferences
    await preferences_col().update_one(
        {"user_id": user_id},
        {
            "$set": {
                "selected_categories": selected_cats,
                "disabled_types": disabled_types,
                "custom_aircraft": custom,
                "updated_at": datetime.now(timezone.utc),
            }
        },
        upsert=True,
    )

    # Mark setup complete
    await users_col().update_one(
        {"user_id": user_id},
        {"$set": {"setup_complete": True}},
    )

    await clear_user_state(user_id)

    # Fetch location for summary
    loc_doc = await locations_col().find_one({"user_id": user_id})
    lat = loc_doc["latitude"] if loc_doc else 0.0
    lon = loc_doc["longitude"] if loc_doc else 0.0
    radius = loc_doc.get("radius_km", settings.default_radius_km) if loc_doc else settings.default_radius_km

    msg = setup_complete_message(
        selected_categories=selected_cats,
        custom_aircraft=custom,
        lat=lat,
        lon=lon,
        radius_km=radius,
    )
    query = update.callback_query
    if query and query.message:
        await query.message.edit_text(msg, parse_mode=ParseMode.HTML)


async def _on_add_custom(update: Update, user_id: int) -> None:
    """Switch to custom aircraft input mode."""
    temp = await get_temp_data(user_id)
    custom: list[str] = temp.get("custom_aircraft", [])

    await set_user_state(user_id, UserState.ADDING_CUSTOM_AIRCRAFT, temp_data=temp)
    query = update.callback_query
    if query and query.message:
        prompt_text = (
            "✏️ <b>Add / Remove Custom Aircraft Code</b>\n\n"
            "Send an ICAO type code (e.g. <code>B738</code>, <code>A21N</code>):\n"
            "• If not in your list: it will be <b>added</b> ✅\n"
            "• If already in your list: it will be <b>removed</b> 🗑️\n\n"
            "Or tap a 🗑️ button below to remove an existing code.\n\n"
            "Send <code>done</code> when finished."
        )
        await query.message.edit_text(
            prompt_text,
            parse_mode=ParseMode.HTML,
            reply_markup=custom_aircraft_keyboard(custom),
        )


async def _on_skip_location(update: Update, user_id: int) -> None:
    """Skip location step and go straight to aircraft selection."""
    temp = await get_temp_data(user_id)
    selected = set(temp.get("selected_categories", []))
    disabled = set(temp.get("disabled_types", []))
    custom = temp.get("custom_aircraft", [])

    await set_user_state(
        user_id,
        UserState.WAITING_AIRCRAFT_SELECTION,
        temp_data={
            "selected_categories": list(selected),
            "disabled_types": list(disabled),
            "custom_aircraft": custom,
        },
    )

    query = update.callback_query
    if query and query.message:
        await query.message.edit_text(
            "⏭️ Location skipped. You can set it later with /location.\n\n"
            + AIRCRAFT_SELECTION_PROMPT,
            parse_mode=ParseMode.HTML,
            reply_markup=aircraft_categories_keyboard(selected, disabled, custom),
        )


# ═══════════════════════════════════════════════════════════════════════════
# MESSAGE HANDLERS
# ═══════════════════════════════════════════════════════════════════════════

async def on_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle a Telegram location message."""
    user = update.effective_user
    msg = update.message
    if user is None or msg is None or msg.location is None:
        return

    state = await get_user_state(user.id)

    lat = msg.location.latitude
    lon = msg.location.longitude
    gh = compute_geohash(lat, lon)

    # Check for existing location to detect location change
    old_loc = await locations_col().find_one({"user_id": user.id})
    old_gh = old_loc.get("geohash", "") if old_loc else ""

    # Save location (do not overwrite existing radius_km if updating)
    await locations_col().update_one(
        {"user_id": user.id},
        {
            "$set": {
                "latitude": lat,
                "longitude": lon,
                "geohash": gh,
                "updated_at": datetime.now(timezone.utc),
            },
            "$setOnInsert": {
                "radius_km": settings.default_radius_km,
            }
        },
        upsert=True,
    )

    # If user changed location, check and reset learning for new region
    if old_gh and old_gh != gh:
        await provider_learner.check_and_handle_location_change(user.id, old_gh, gh)

    # Confirm
    await msg.reply_text(
        LOCATION_SAVED.format(lat=lat, lon=lon),
        parse_mode=ParseMode.HTML,
    )

    # If in setup flow (or /location command), advance to radius prompt
    if state == UserState.WAITING_LOCATION:
        await set_user_state(user.id, UserState.WAITING_RADIUS)
        await msg.reply_text(RADIUS_PROMPT, parse_mode=ParseMode.HTML)
    elif state != UserState.IDLE:
        # Location update outside normal flow — go idle
        await clear_user_state(user.id)


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle free-text messages (mainly custom ICAO codes)."""
    user = update.effective_user
    msg = update.message
    if user is None or msg is None or msg.text is None:
        return

    state = await get_user_state(user.id)

    if state == UserState.WAITING_RADIUS:
        await _handle_radius_input(update, user.id, msg.text.strip())
    elif state == UserState.ADDING_CUSTOM_AIRCRAFT:
        await _handle_custom_aircraft_input(update, user.id, msg.text.strip())
    elif state == UserState.IDLE:
        # User sent a random message — show help hint
        await msg.reply_text(
            "I didn't understand that. Use /help to see available commands.",
            parse_mode=ParseMode.HTML,
        )
    else:
        # In some other state but got text — ignore gracefully
        await msg.reply_text(
            "Please complete the current step or use /cancel to abort.",
            parse_mode=ParseMode.HTML,
        )


async def _handle_radius_input(
    update: Update, user_id: int, text: str
) -> None:
    """Process radius input and advance to aircraft selection."""
    msg = update.message
    if msg is None:
        return

    try:
        radius = float(text)
        if not (1 <= radius <= 150):
            raise ValueError
    except ValueError:
        await msg.reply_text(INVALID_RADIUS, parse_mode=ParseMode.HTML)
        return

    # Save to database
    await locations_col().update_one(
        {"user_id": user_id},
        {"$set": {"radius_km": radius}}
    )

    # Advance to next step
    temp = await get_temp_data(user_id)
    selected = set(temp.get("selected_categories", []))
    custom = temp.get("custom_aircraft", [])

    await set_user_state(
        user_id,
        UserState.WAITING_AIRCRAFT_SELECTION,
        temp_data={"selected_categories": list(selected), "custom_aircraft": custom},
    )
    await msg.reply_text(
        AIRCRAFT_SELECTION_PROMPT,
        parse_mode=ParseMode.HTML,
        reply_markup=aircraft_categories_keyboard(selected),
    )


async def _handle_custom_aircraft_input(
    update: Update, user_id: int, text: str
) -> None:
    """Process a custom ICAO code input."""
    msg = update.message
    if msg is None:
        return

    # "done" exits custom input mode
    if text.lower() == "done":
        temp = await get_temp_data(user_id)
        selected = set(temp.get("selected_categories", []))
        disabled = set(temp.get("disabled_types", []))
        custom = temp.get("custom_aircraft", [])

        await set_user_state(
            user_id,
            UserState.WAITING_AIRCRAFT_SELECTION,
            temp_data={
                "selected_categories": list(selected),
                "disabled_types": list(disabled),
                "custom_aircraft": custom,
            },
        )
        await msg.reply_text(
            AIRCRAFT_SELECTION_PROMPT,
            parse_mode=ParseMode.HTML,
            reply_markup=aircraft_categories_keyboard(selected, disabled, custom),
        )
        return

    code = text.upper()
    if not validate_icao_code(code):
        await msg.reply_text(
            INVALID_ICAO_CODE.format(code=text), parse_mode=ParseMode.HTML
        )
        return

    temp = await get_temp_data(user_id)
    custom: list[str] = temp.get("custom_aircraft", [])

    if code in custom:
        custom.remove(code)
        await update_temp_data(user_id, {"custom_aircraft": custom})
        await msg.reply_text(
            f"🗑️ Removed <code>{code}</code> from your custom types.\n"
            "Send another code, or send <code>done</code> to finish.",
            parse_mode=ParseMode.HTML,
            reply_markup=custom_aircraft_keyboard(custom),
        )
    else:
        custom.append(code)
        await update_temp_data(user_id, {"custom_aircraft": custom})
        await msg.reply_text(
            f"✅ Added <code>{code}</code> to your custom types.\n"
            "Send another code, or send <code>done</code> to finish.",
            parse_mode=ParseMode.HTML,
            reply_markup=custom_aircraft_keyboard(custom),
        )
