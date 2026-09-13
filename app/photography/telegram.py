"""Telegram commands and callbacks for the v3.2 photography assistant."""
from __future__ import annotations

import logging
from html import escape

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application, ApplicationHandlerStop, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters,
)

from app.bot.states import UserState, clear_user_state, get_temp_data, set_user_state
from app.photography.formatting import (
    camera_profile_message,
    conditions_message,
    lens_profile_message,
    recommendation_message,
)
from app.photography.gemini import GeminiPhotographyError
from app.photography.keyboards import PHOTO_CALLBACK_PREFIX
from app.photography.service import (
    MissingCameraError,
    MissingLocationError,
    get_camera_setup,
    get_conditions_for_user,
    identify_and_save_camera,
    identify_and_save_lens,
    recommend_for_user,
)

logger = logging.getLogger(__name__)
_CAMERA_PROMPT = (
    "📷 <b>Camera setup</b>\n\n"
    "Type the exact camera body you use. Gemini will identify it and build a "
    "camera profile for aviation photography.\n\n"
    "Examples:\n"
    "• <code>Sony A7 IV</code>\n"
    "• <code>Canon R7</code>\n"
    "• <code>Nikon Z8</code>\n"
    "• <code>iPhone 17 Pro</code>\n\n"
    "You can include extra detail if the name is ambiguous."
)

_LENS_PROMPT = (
    "🔭 <b>Lens setup</b>\n\n"
    "Type the lens you normally use for aircraft. Gemini will resolve the focal "
    "range, aperture and stabilization conservatively.\n\n"
    "Examples: <code>Sony 200-600 G</code>, <code>RF 100-500 L</code>, "
    "<code>Nikon Z 180-600</code>."
)


def register_photography_handlers(app: Application) -> None:
    """Register v3.2 handlers in an earlier group than legacy catch-alls."""
    app.add_handler(CommandHandler("help", cmd_photo_help), group=-1)
    app.add_handler(CommandHandler("camera", cmd_camera), group=-1)
    app.add_handler(CommandHandler("lens", cmd_lens), group=-1)
    app.add_handler(CommandHandler("photo", cmd_photo), group=-1)
    app.add_handler(CommandHandler("conditions", cmd_conditions), group=-1)
    app.add_handler(CallbackQueryHandler(handle_photo_callback, pattern=rf"^{PHOTO_CALLBACK_PREFIX}"), group=-1)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _photo_text_router), group=-1)


async def cmd_photo_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Extended help that owns /help before the legacy group-0 handler."""
    if update.message is None:
        raise ApplicationHandlerStop
    await update.message.reply_text(
        "✈️ <b>Aircraft Alert v3.2</b>\n\n"
        "<b>Aircraft monitoring</b>\n"
        "/start — initial setup\n"
        "/status — current monitoring configuration\n"
        "/location — update shooting/monitoring location\n"
        "/preferences — aircraft categories and types\n\n"
        "<b>Gemini photography</b>\n"
        "/camera — tell Gemini your camera body\n"
        "/lens — tell Gemini your aircraft lens\n"
        "/conditions — measured weather, atmosphere and sun geometry\n"
        "/photo — live best-shot analysis and exact camera setup\n\n"
        "Aircraft alerts also include a <b>📷 Best camera settings</b> button. "
        "Gemini combines your gear with live aircraft data, Open-Meteo conditions and solar geometry.",
        parse_mode=ParseMode.HTML,
    )
    raise ApplicationHandlerStop


async def _photo_text_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    msg = update.message
    if user is None or msg is None or msg.text is None:
        return
    from app.bot.states import get_user_state
    state = await get_user_state(user.id)
    consumed = await handle_photography_text(update, user.id, msg.text.strip(), state)
    if consumed:
        raise ApplicationHandlerStop


async def cmd_camera(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    msg = update.message
    if user is None or msg is None:
        return
    current, lens = await get_camera_setup(user.id)
    temp: dict[str, object] = {}
    if lens:
        temp["existing_lens"] = lens.model_dump(mode="json")
    await set_user_state(user.id, UserState.WAITING_CAMERA, temp_data=temp)
    if current:
        name = " ".join(p for p in (current.brand, current.model) if p).strip() or current.raw_input
        await msg.reply_text(
            f"Current camera: <b>{escape(name, quote=True)}</b>\n\nSend a new camera name to replace it.\n\n" + _CAMERA_PROMPT,
            parse_mode=ParseMode.HTML,
        )
    else:
        await msg.reply_text(_CAMERA_PROMPT, parse_mode=ParseMode.HTML)


async def cmd_lens(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    msg = update.message
    if user is None or msg is None:
        return
    camera, current = await get_camera_setup(user.id)
    if camera is None:
        await set_user_state(user.id, UserState.WAITING_CAMERA, temp_data={"lens_after_camera": True})
        await msg.reply_text(
            "I need your camera body first so Gemini can interpret the lens correctly.\n\n" + _CAMERA_PROMPT,
            parse_mode=ParseMode.HTML,
        )
        return
    await set_user_state(user.id, UserState.WAITING_LENS, temp_data={})
    prefix = ""
    if current:
        name = " ".join(p for p in (current.brand, current.model) if p).strip() or current.raw_input
        prefix = f"Current lens: <b>{escape(name, quote=True)}</b>\n\nSend a new lens to replace it.\n\n"
    await msg.reply_text(prefix + _LENS_PROMPT, parse_mode=ParseMode.HTML)


async def cmd_conditions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    msg = update.message
    if user is None or msg is None:
        return
    progress = await msg.reply_text("🌤️ Reading weather, visibility, atmosphere and sun position…")
    try:
        weather, solar = await get_conditions_for_user(user.id)
        await progress.edit_text(conditions_message(weather, solar), parse_mode=ParseMode.HTML)
    except MissingLocationError:
        await progress.edit_text("📍 Set your location first with /location.")
    except Exception:
        logger.exception("Photography conditions failed for user %s", user.id)
        await progress.edit_text("⚠️ Conditions could not be loaded right now. Please try again shortly.")


async def cmd_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    msg = update.message
    if user is None or msg is None:
        return
    camera, _ = await get_camera_setup(user.id)
    if camera is None:
        await set_user_state(user.id, UserState.WAITING_CAMERA, temp_data={"photo_after_camera": True})
        await msg.reply_text(
            "To calculate real settings, I first need to know your camera.\n\n" + _CAMERA_PROMPT,
            parse_mode=ParseMode.HTML,
        )
        return
    await _send_recommendation(user.id, msg)


async def handle_photo_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    if query is None or user is None or query.data is None:
        return
    await query.answer("Analyzing shooting conditions…")
    notification_id = query.data[len(PHOTO_CALLBACK_PREFIX):].strip()
    if not query.message:
        raise ApplicationHandlerStop
    camera, _ = await get_camera_setup(user.id)
    if camera is None:
        await set_user_state(
            user.id,
            UserState.WAITING_CAMERA,
            temp_data={"photo_notification_id": notification_id},
        )
        await query.message.reply_text(
            "📷 I can calculate settings for this aircraft, but first tell me your camera body.\n\n" + _CAMERA_PROMPT,
            parse_mode=ParseMode.HTML,
        )
        raise ApplicationHandlerStop
    await _send_recommendation(user.id, query.message, notification_id=notification_id)
    raise ApplicationHandlerStop


async def handle_photography_text(update: Update, user_id: int, text: str, state: UserState) -> bool:
    """Handle camera/lens text states. Returns True when the text was consumed."""
    if state == UserState.WAITING_CAMERA:
        await _handle_camera_text(update, user_id, text)
        return True
    if state == UserState.WAITING_LENS:
        await _handle_lens_text(update, user_id, text)
        return True
    return False


async def _handle_camera_text(update: Update, user_id: int, text: str) -> None:
    msg = update.message
    if msg is None:
        return
    if len(text.strip()) < 2:
        await msg.reply_text("Please type a camera model, for example <code>Canon R7</code>.", parse_mode=ParseMode.HTML)
        return
    pending = await get_temp_data(user_id)
    progress = await msg.reply_text("🧠 Gemini is identifying your camera and its aviation-photo capabilities…")
    try:
        camera = await identify_and_save_camera(user_id, text.strip())
    except GeminiPhotographyError as exc:
        logger.warning("Camera identification failed for user %s: %s", user_id, exc)
        await progress.edit_text("⚠️ Gemini couldn't identify that camera right now. Your input was not saved; send it again to retry.")
        return
    except Exception:
        logger.exception("Camera identification failed for user %s", user_id)
        await progress.edit_text("⚠️ Camera identification failed unexpectedly. Send the camera name again to retry.")
        return
    await progress.edit_text(camera_profile_message(camera), parse_mode=ParseMode.HTML)
    notification_id = str(pending.get("photo_notification_id") or "")
    photo_after = bool(pending.get("photo_after_camera")) or bool(notification_id)
    lens_after = bool(pending.get("lens_after_camera"))
    if lens_after:
        await set_user_state(user_id, UserState.WAITING_LENS, temp_data={})
        await msg.reply_text(_LENS_PROMPT, parse_mode=ParseMode.HTML)
        return
    await clear_user_state(user_id)
    if photo_after:
        await _send_recommendation(user_id, msg, notification_id=notification_id)


async def _handle_lens_text(update: Update, user_id: int, text: str) -> None:
    msg = update.message
    if msg is None:
        return
    if len(text.strip()) < 2:
        await msg.reply_text("Please type a lens model, for example <code>RF 100-500 L</code>.", parse_mode=ParseMode.HTML)
        return
    progress = await msg.reply_text("🧠 Gemini is identifying your lens…")
    try:
        lens = await identify_and_save_lens(user_id, text.strip())
    except GeminiPhotographyError as exc:
        logger.warning("Lens identification failed for user %s: %s", user_id, exc)
        await progress.edit_text("⚠️ Gemini couldn't identify that lens right now. Send it again to retry.")
        return
    except Exception:
        logger.exception("Lens identification failed for user %s", user_id)
        await progress.edit_text("⚠️ Lens identification failed unexpectedly. Send it again to retry.")
        return
    await clear_user_state(user_id)
    await progress.edit_text(lens_profile_message(lens), parse_mode=ParseMode.HTML)


async def _send_recommendation(user_id: int, message, *, notification_id: str = "") -> None:
    progress = await message.reply_text(
        "📡 Checking live aircraft + Open-Meteo weather/air quality + sun geometry…\n"
        "🧠 Then Gemini will choose the camera setup."
    )
    try:
        rec, photo_context = await recommend_for_user(user_id, notification_id=notification_id)
        await progress.edit_text(
            recommendation_message(rec, photo_context),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except MissingLocationError:
        await progress.edit_text("📍 I need your shooting location first. Use /location and send it from Telegram.")
    except MissingCameraError:
        await set_user_state(user_id, UserState.WAITING_CAMERA, temp_data={"photo_notification_id": notification_id})
        await progress.edit_text("📷 I need your camera first. Type the exact camera body you use.")
    except GeminiPhotographyError as exc:
        logger.warning("Photo recommendation failed for user %s: %s", user_id, exc)
        await progress.edit_text(
            "⚠️ Gemini is unavailable or couldn't return a trustworthy structured result. "
            "v3.2 deliberately does not substitute scripted camera settings. Try again shortly."
        )
    except Exception:
        logger.exception("Photo recommendation failed for user %s", user_id)
        await progress.edit_text("⚠️ The photography analysis failed unexpectedly. Please try /photo again.")
