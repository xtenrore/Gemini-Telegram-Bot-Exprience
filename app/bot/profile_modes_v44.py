"""v4.4 profile setup mode chooser and contextual navigation."""
from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, WebAppInfo
from telegram.ext import Application, ApplicationHandlerStop, CallbackQueryHandler, CommandHandler, ContextTypes

from app.alert_profiles import ensure_default_profile, get_profile
from app.bot import profile_handlers as guided
from app.config import settings


def _visual_url(profile_id: str = "", *, new_profile: bool = False) -> str:
    base = settings.webhook_url.strip().rstrip("/")
    if not base:
        return ""
    if new_profile:
        return f"{base}/profile-setup-ui/new"
    return f"{base}/profile-setup-ui/profile/{profile_id}"


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
        "Guided Setup uses the familiar Telegram message flow.\n"
        "Visual Setup opens the graphical profile editor. Both edit the same profile settings."
    )
    await guided._show(update, text, InlineKeyboardMarkup(rows))


async def intercept_profile_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    if not query or not user or not query.data:
        return
    data = query.data
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
    await query.answer()
    data = query.data
    if data == "pf44:profiles":
        await guided._clear_state(user.id)
        await guided._render_profiles(update, user.id)
    elif data == "pf44:gnew":
        await guided._start_create(update, user.id)
    elif data.startswith("pf44:guided:"):
        profile_id = data.split(":", 2)[2]
        await guided._start_edit(update, user.id, profile_id)
    elif data == "pf44:visual_unavailable":
        await guided._show(
            update,
            "<b>Visual Setup unavailable</b>\n\nThe public HTTPS app URL is not configured. Guided Setup remains fully available.",
            InlineKeyboardMarkup([[InlineKeyboardButton("← Back to Profiles", callback_data="pf44:profiles")]]),
        )
    raise ApplicationHandlerStop


async def cmd_preferences_v44(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        return
    active = await ensure_default_profile(user.id)
    await _show_mode(update, str(active["profile_id"]))
    raise ApplicationHandlerStop


def register_profile_mode_handlers_v44(app: Application) -> None:
    # One group before the existing v4.3 profile router: only the entry points
    # are intercepted; the proven Guided Setup state machine remains unchanged.
    group = -31
    app.add_handler(CommandHandler("preferences", cmd_preferences_v44), group=group)
    app.add_handler(CallbackQueryHandler(intercept_profile_callback, pattern=r"^pf:(?:new|e:)"), group=group)
    app.add_handler(CallbackQueryHandler(profile_mode_callback, pattern=r"^pf44:"), group=group)
