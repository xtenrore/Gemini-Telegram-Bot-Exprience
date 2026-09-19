"""Compatibility routing from legacy setup commands into v4.3 profiles."""
from __future__ import annotations

from copy import deepcopy

from telegram import InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.alert_profiles import blank_config_from, ensure_default_profile, save_profile
from app.bot.profile_handlers import _button, _clear_state, _set_state
from app.database import users_col, user_state_col
from app.worker.geo import compute_geohash


async def _state(user_id: int) -> tuple[str, dict]:
    doc = await user_state_col().find_one({"user_id": int(user_id)})
    return (str((doc or {}).get("current_state") or "idle"), dict((doc or {}).get("temp_data") or {}))


async def _begin_profile_setup(update: Update, user_id: int, *, edit_message: bool = False) -> None:
    active = await ensure_default_profile(user_id)
    temp = {
        "profile_flow": "setup",
        "editing_profile_id": active["profile_id"],
        "draft_name": active.get("name") or "Default",
        "profile_draft": blank_config_from(active.get("config") or {}),
        "profile_return": "edit",
    }
    await _set_state(user_id, "profile:location", temp)
    text = (
        "<b>Set Up Plane Alerts</b>\n\n"
        "Send the Telegram location you want this profile to monitor."
    )
    keyboard = InlineKeyboardMarkup([[_button("Cancel", "pf:home")]])
    query = update.callback_query
    if edit_message and query and query.message:
        await query.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
    elif update.message:
        await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
    elif query and query.message:
        await query.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)


async def cmd_setup_profiled(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        return
    await _begin_profile_setup(update, user.id)
    raise ApplicationHandlerStop


async def cmd_location_profiled(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        return
    active = await ensure_default_profile(user.id)
    await _set_state(
        user.id,
        "profile:quick_location",
        {
            "editing_profile_id": active["profile_id"],
            "profile_draft": deepcopy(active.get("config") or {}),
        },
    )
    if update.message:
        await update.message.reply_text(
            "<b>Update Location</b>\n\nSend the Telegram location for the active profile.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[_button("Cancel", "pf:home")]]),
        )
    raise ApplicationHandlerStop


async def accept_terms_profiled(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Keep the existing welcome/disclaimer but use the v4.3 setup after it."""
    query = update.callback_query
    user = update.effective_user
    if not query or not user:
        return
    await query.answer()
    await users_col().update_one(
        {"user_id": user.id},
        {"$set": {"terms_accepted": True}},
        upsert=True,
    )
    await _begin_profile_setup(update, user.id, edit_message=True)
    raise ApplicationHandlerStop


async def quick_location_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.message
    if not user or not message or not message.location:
        return
    state, temp = await _state(user.id)
    if state != "profile:quick_location":
        return
    profile_id = str(temp.get("editing_profile_id") or "")
    config = deepcopy(temp.get("profile_draft") or {})
    loc = dict(config.get("location") or {})
    lat = float(message.location.latitude)
    lon = float(message.location.longitude)
    loc.update({"latitude": lat, "longitude": lon, "geohash": compute_geohash(lat, lon)})
    config["location"] = loc
    profile = await save_profile(user.id, profile_id, config=config)
    await _clear_state(user.id)
    await message.reply_text(
        f"Location updated for <b>{profile.get('name') or 'active profile'}</b>.",
        parse_mode=ParseMode.HTML,
    )
    raise ApplicationHandlerStop


def register_profile_legacy_handlers(app: Application) -> None:
    # Run before the v4.3 general profile handlers (-30) and old handlers (0).
    group = -31
    app.add_handler(CommandHandler("setup", cmd_setup_profiled), group=group)
    app.add_handler(CommandHandler("location", cmd_location_profiled), group=group)
    app.add_handler(CallbackQueryHandler(accept_terms_profiled, pattern=r"^terms:accept$"), group=group)
    app.add_handler(MessageHandler(filters.LOCATION, quick_location_message), group=group)
