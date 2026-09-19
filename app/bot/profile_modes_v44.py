"""v4.4 profile setup mode chooser and contextual navigation."""
from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, WebAppInfo
from telegram.ext import Application, ApplicationHandlerStop, CallbackQueryHandler, CommandHandler, ContextTypes

from app.alert_profiles import (
    activate_profile,
    ensure_default_profile,
    get_active_profile,
    get_profile,
    list_profiles,
    profile_summary,
)
from app.bot import profile_handlers as guided
from app.config import settings

logger = logging.getLogger(__name__)


def _visual_url(profile_id: str = "", *, new_profile: bool = False) -> str:
    base = settings.webhook_url.strip().rstrip("/")
    if not base:
        return ""
    if new_profile:
        return f"{base}/profile-setup-ui/new"
    return f"{base}/profile-setup-ui/profile/{profile_id}"


async def _render_profiles_v44(update: Update, user_id: int) -> None:
    """Render the v4.4 profile home directly instead of delegating to v4.3 UI."""
    await ensure_default_profile(user_id)
    active = await get_active_profile(user_id)
    profiles = await list_profiles(user_id)
    active_id = str((active or {}).get("profile_id") or "")

    lines = ["<b>Profiles · v4.4</b>", "", "Tap a profile name to make it active. Use Edit to choose Guided or Visual Setup.", ""]
    rows: list[list[InlineKeyboardButton]] = []
    for profile in profiles:
        profile_id = str(profile.get("profile_id") or "")
        name = str(profile.get("name") or "Profile")
        marker = "✓ " if profile_id == active_id else ""
        lines.append(f"<b>{marker}{guided._esc(name)}</b>")
        lines.append(guided._esc(profile_summary(profile)))
        lines.append("")
        rows.append([
            InlineKeyboardButton(f"{marker}{name}", callback_data=f"pf44:activate:{profile_id}"),
            InlineKeyboardButton("Edit", callback_data=f"pf44:edit:{profile_id}"),
        ])

    rows.append([InlineKeyboardButton("＋ Create Profile", callback_data="pf44:new")])
    await guided._show(update, "\n".join(lines).rstrip(), InlineKeyboardMarkup(rows))


async def _show_mode(update: Update, profile_id: str, *, new_profile: bool = False) -> None:
    query = update.callback_query
    if query:
        await query.answer()
    url = _visual_url(profile_id, new_profile=new_profile)
    guided_callback = "pf44:gnew" if new_profile else f"pf44:guided:{profile_id}"
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton("Guided Setup", callback_data=guided_callback)],
    ]
    if url:
        rows.append([InlineKeyboardButton("Visual Setup", web_app=WebAppInfo(url=url))])
    else:
        rows.append([InlineKeyboardButton("Visual Setup", callback_data="pf44:visual_unavailable")])
    rows.append([InlineKeyboardButton("← Back to Profiles", callback_data="pf44:profiles")])
    text = (
        "<b>Choose setup method</b>\n\n"
        "Guided Setup uses Telegram messages and buttons.\n"
        "Visual Setup opens the graphical editor. Both edit the same profile settings."
    )
    await guided._show(update, text, InlineKeyboardMarkup(rows))


async def cmd_profiles_v44(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        return
    logger.info("v44_profiles_command")
    await guided._clear_state(user.id)
    await _render_profiles_v44(update, user.id)
    raise ApplicationHandlerStop


async def intercept_profile_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Catch v4.3 entry callbacks from old messages and upgrade them in-place."""
    query = update.callback_query
    user = update.effective_user
    if not query or not user or not query.data:
        return
    data = query.data
    logger.info("v44_profile_entry callback=%s", data.split(":", 2)[0:2])
    if data == "pf:new":
        active = await ensure_default_profile(user.id)
        await _show_mode(update, str(active["profile_id"]), new_profile=True)
    elif data.startswith("pf:e:"):
        profile_id = data.split(":", 2)[2]
        if await get_profile(user.id, profile_id) is None:
            await guided._stale(update, user.id)
        else:
            await _show_mode(update, profile_id)
    else:
        return
    raise ApplicationHandlerStop


async def profile_mode_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    if not query or not user or not query.data:
        return
    data = query.data
    logger.info("v44_profile_callback action=%s", data.split(":", 2)[:2])

    if data == "pf44:profiles":
        await query.answer()
        await guided._clear_state(user.id)
        await _render_profiles_v44(update, user.id)
    elif data == "pf44:new":
        active = await ensure_default_profile(user.id)
        await _show_mode(update, str(active["profile_id"]), new_profile=True)
    elif data.startswith("pf44:activate:"):
        await query.answer()
        profile_id = data.split(":", 2)[2]
        profile = await activate_profile(user.id, profile_id)
        await guided._clear_state(user.id)
        await _render_profiles_v44(update, user.id)
        await query.answer(f"Active: {profile.get('name')}")
    elif data.startswith("pf44:edit:"):
        profile_id = data.split(":", 2)[2]
        if await get_profile(user.id, profile_id) is None:
            await query.answer("This profile no longer exists.", show_alert=True)
            await _render_profiles_v44(update, user.id)
        else:
            await _show_mode(update, profile_id)
    elif data == "pf44:gnew":
        await query.answer()
        await guided._start_create(update, user.id)
    elif data.startswith("pf44:guided:"):
        await query.answer()
        profile_id = data.split(":", 2)[2]
        await guided._start_edit(update, user.id, profile_id)
    elif data == "pf44:visual_unavailable":
        await query.answer()
        await guided._show(
            update,
            "<b>Visual Setup unavailable</b>\n\nThe public HTTPS app URL is not configured. Guided Setup remains fully available.",
            InlineKeyboardMarkup([[InlineKeyboardButton("← Back to Profiles", callback_data="pf44:profiles")]]),
        )
    else:
        return
    raise ApplicationHandlerStop


async def cmd_preferences_v44(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        return
    logger.info("v44_preferences_command")
    active = await ensure_default_profile(user.id)
    await _show_mode(update, str(active["profile_id"]))
    raise ApplicationHandlerStop


def register_profile_mode_handlers_v44(app: Application) -> None:
    """Install all visible v4.4 profile entry points before the v4.3 router."""
    group = -31
    app.add_handler(CommandHandler("profiles", cmd_profiles_v44), group=group)
    app.add_handler(CommandHandler("preferences", cmd_preferences_v44), group=group)
    app.add_handler(CallbackQueryHandler(intercept_profile_callback, pattern=r"^pf:(?:new|e:)"), group=group)
    app.add_handler(CallbackQueryHandler(profile_mode_callback, pattern=r"^pf44:"), group=group)
    logger.info("Plane Alerts v4.4 Telegram profile handlers registered in group %d", group)
