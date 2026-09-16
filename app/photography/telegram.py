"""Telegram commands/callbacks for Plane? v3.4 spotting intelligence."""
from __future__ import annotations
import logging
from html import escape
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, ApplicationHandlerStop, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters
from app.bot.states import UserState, clear_user_state, get_temp_data, set_user_state
from app.database import preferences_col
from app.photography.formatting import camera_profile_message, conditions_message, lens_profile_message, recommendation_message
from app.photography.keyboards import PHOTO_CALLBACK_PREFIX
from app.photography.service import MissingCameraError, MissingLocationError, get_camera_setup, get_conditions_for_user, identify_and_save_camera, identify_and_save_lens, recommend_for_user
logger=logging.getLogger(__name__)
_CAMERA_PROMPT="📷 <b>Camera setup</b>\n\nType the exact camera body you use. Plane? resolves known camera geometry locally, so critical settings do not depend on AI.\n\nExamples: <code>Canon EOS R7</code>, <code>Nikon Z8</code>, <code>Sony A1</code>."
_LENS_PROMPT="🔭 <b>Lens setup</b>\n\nType the lens you use for aircraft. Known lenses are profiled locally; unknown zooms can still be parsed from a range such as <code>150-600mm</code>.\n\nExample: <code>Canon RF 200-800mm</code>."
_MODES={"maximum-detail":"Maximum detail","standard":"Standard aviation","standard-aviation":"Standard aviation","prop-blur":"Prop blur","night":"Night aircraft","night-aircraft":"Night aircraft","silhouette":"Silhouette","contrail":"Contrail shot","contrail-shot":"Contrail shot","emergency":"Emergency quick setup","emergency-quick-setup":"Emergency quick setup"}

def register_photography_handlers(app:Application)->None:
    app.add_handler(CommandHandler("help",cmd_photo_help),group=-1); app.add_handler(CommandHandler("camera",cmd_camera),group=-1); app.add_handler(CommandHandler("lens",cmd_lens),group=-1); app.add_handler(CommandHandler("photo",cmd_photo),group=-1); app.add_handler(CommandHandler("conditions",cmd_conditions),group=-1); app.add_handler(CommandHandler("spotting",cmd_spotting),group=-1); app.add_handler(CallbackQueryHandler(handle_photo_callback,pattern=rf"^{PHOTO_CALLBACK_PREFIX}"),group=-1); app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,_photo_text_router),group=-1)

async def cmd_photo_help(update:Update,context:ContextTypes.DEFAULT_TYPE)->None:
    if update.message is None:raise ApplicationHandlerStop
    await update.message.reply_text("✈️ <b>Plane? v3.4 — Spotting Intelligence</b>\n\n<b>Monitoring</b>\n/start — setup\n/status — current config\n/location — update observer location\n/preferences — aircraft filters\n\n<b>Spotting</b>\n/camera — camera body\n/lens — aircraft lens\n/conditions — measured atmosphere and Sun\n/photo — deterministic live camera setup\n/spotting — modes and alert controls\n\nTrajectory/CPA, framing, shutter floor, Sun geometry, atmosphere and contrail estimates are deterministic. Gemini is optional and only explains the result.",parse_mode=ParseMode.HTML); raise ApplicationHandlerStop

async def _photo_text_router(update:Update,context:ContextTypes.DEFAULT_TYPE)->None:
    user=update.effective_user; msg=update.message
    if user is None or msg is None or msg.text is None:return
    from app.bot.states import get_user_state
    if await handle_photography_text(update,user.id,msg.text.strip(),await get_user_state(user.id)):raise ApplicationHandlerStop

async def cmd_camera(update:Update,context:ContextTypes.DEFAULT_TYPE)->None:
    user=update.effective_user; msg=update.message
    if user is None or msg is None:return
    current,lens=await get_camera_setup(user.id); temp={}
    if lens:temp["existing_lens"]=lens.model_dump(mode="json")
    await set_user_state(user.id,UserState.WAITING_CAMERA,temp_data=temp); prefix=""
    if current:
        name=" ".join(x for x in (current.brand,current.model) if x).strip() or current.raw_input; prefix=f"Current camera: <b>{escape(name,quote=True)}</b>\n\nSend a new body to replace it.\n\n"
    await msg.reply_text(prefix+_CAMERA_PROMPT,parse_mode=ParseMode.HTML)

async def cmd_lens(update:Update,context:ContextTypes.DEFAULT_TYPE)->None:
    user=update.effective_user; msg=update.message
    if user is None or msg is None:return
    camera,current=await get_camera_setup(user.id)
    if camera is None:
        await set_user_state(user.id,UserState.WAITING_CAMERA,temp_data={"lens_after_camera":True}); await msg.reply_text("I need the camera body first.\n\n"+_CAMERA_PROMPT,parse_mode=ParseMode.HTML); return
    await set_user_state(user.id,UserState.WAITING_LENS,temp_data={}); prefix=""
    if current:
        name=" ".join(x for x in (current.brand,current.model) if x).strip() or current.raw_input; prefix=f"Current lens: <b>{escape(name,quote=True)}</b>\n\nSend a new lens to replace it.\n\n"
    await msg.reply_text(prefix+_LENS_PROMPT,parse_mode=ParseMode.HTML)

async def cmd_conditions(update:Update,context:ContextTypes.DEFAULT_TYPE)->None:
    user=update.effective_user; msg=update.message
    if user is None or msg is None:return
    progress=await msg.reply_text("🌤️ Reading weather, visibility, atmosphere and Sun position…")
    try:
        weather,solar=await get_conditions_for_user(user.id); await progress.edit_text(conditions_message(weather,solar),parse_mode=ParseMode.HTML)
    except MissingLocationError:await progress.edit_text("📍 Set your location first with /location.")
    except Exception:logger.exception("conditions_failed user=%s",user.id); await progress.edit_text("⚠️ Conditions could not be loaded right now.")

async def cmd_photo(update:Update,context:ContextTypes.DEFAULT_TYPE)->None:
    user=update.effective_user; msg=update.message
    if user is None or msg is None:return
    camera,_=await get_camera_setup(user.id)
    if camera is None:
        await set_user_state(user.id,UserState.WAITING_CAMERA,temp_data={"photo_after_camera":True}); await msg.reply_text("I need your camera geometry first.\n\n"+_CAMERA_PROMPT,parse_mode=ParseMode.HTML); return
    await _send_recommendation(user.id,msg)

def _bool(v:str)->bool|None:
    v=v.lower().strip()
    if v in {"on","true","yes","1"}:return True
    if v in {"off","false","no","0"}:return False
    return None

async def cmd_spotting(update:Update,context:ContextTypes.DEFAULT_TYPE)->None:
    user=update.effective_user; msg=update.message
    if user is None or msg is None:return
    prefs=await preferences_col().find_one({"user_id":user.id}) or {}; spot=dict(prefs.get("spotting") or {})
    if context.args:
        errors=[]
        for token in context.args:
            if "=" not in token:errors.append(token);continue
            k,v=token.split("=",1); k=k.lower().strip(); v=v.strip()
            if k=="mode":
                mode=_MODES.get(v.lower().replace("_","-")); spot["mode"]=mode if mode else spot.get("mode","Standard aviation"); errors.extend([] if mode else [token])
            elif k in {"iso","maxiso"}:
                try:spot["max_auto_iso"]=max(100,min(51200,int(v)))
                except ValueError:errors.append(token)
            elif k in {"approach","ready","photo","contrail","moon","solar"}:
                val=_bool(v)
                if val is None:errors.append(token);continue
                spot[{"approach":"approach_alerts","ready":"camera_ready_alerts","photo":"photo_now_alerts","contrail":"contrail_alerts","moon":"moon_crossing_alerts","solar":"solar_crossing_alerts"}[k]]=val
            elif k=="confidence" and v.capitalize() in {"High","Medium","Low","Uncertain"}:spot["min_confidence"]=v.capitalize()
            else:errors.append(token)
        await preferences_col().update_one({"user_id":user.id},{"$set":{"spotting":spot}},upsert=True)
        if errors:await msg.reply_text("Ignored invalid setting(s): "+", ".join(escape(x,quote=True) for x in errors),parse_mode=ParseMode.HTML)
    defaults={"mode":"Standard aviation","max_auto_iso":1600,"approach_alerts":True,"camera_ready_alerts":True,"photo_now_alerts":True,"contrail_alerts":True,"moon_crossing_alerts":True,"solar_crossing_alerts":False,"min_confidence":"Low"}; defaults.update(spot)
    await msg.reply_text("🎯 <b>Spotting Mode settings</b>\n"+f"Mode: <b>{escape(str(defaults['mode']))}</b>\nMax Auto ISO: <b>{defaults['max_auto_iso']}</b>\nMin trajectory confidence: <b>{defaults['min_confidence']}</b>\n\n"+f"Approach: {'on' if defaults['approach_alerts'] else 'off'} · Camera ready: {'on' if defaults['camera_ready_alerts'] else 'off'} · PHOTO NOW: {'on' if defaults['photo_now_alerts'] else 'off'}\n"+f"Contrail: {'on' if defaults['contrail_alerts'] else 'off'} · Moon crossing: {'on' if defaults['moon_crossing_alerts'] else 'off'} · Solar crossing: {'on' if defaults['solar_crossing_alerts'] else 'off'}\n\n"+"Change with: <code>/spotting mode=maximum-detail iso=1600 confidence=medium photo=on moon=on solar=off</code>\nModes: standard, maximum-detail, prop-blur, night, silhouette, contrail, emergency.\n\n☀️ Solar-crossing alerts are off by default and always include solar-viewing safety guidance.",parse_mode=ParseMode.HTML)

async def handle_photo_callback(update:Update,context:ContextTypes.DEFAULT_TYPE)->None:
    query=update.callback_query; user=update.effective_user
    if query is None or user is None or query.data is None:return
    await query.answer("Calculating deterministic spotting setup…"); nid=query.data[len(PHOTO_CALLBACK_PREFIX):].strip()
    if not query.message:raise ApplicationHandlerStop
    camera,_=await get_camera_setup(user.id)
    if camera is None:
        await set_user_state(user.id,UserState.WAITING_CAMERA,temp_data={"photo_notification_id":nid}); await query.message.reply_text("📷 Tell me your camera body first.\n\n"+_CAMERA_PROMPT,parse_mode=ParseMode.HTML); raise ApplicationHandlerStop
    await _send_recommendation(user.id,query.message,notification_id=nid); raise ApplicationHandlerStop

async def handle_photography_text(update:Update,user_id:int,text:str,state:UserState)->bool:
    if state==UserState.WAITING_CAMERA:await _handle_camera_text(update,user_id,text);return True
    if state==UserState.WAITING_LENS:await _handle_lens_text(update,user_id,text);return True
    return False

async def _handle_camera_text(update:Update,user_id:int,text:str)->None:
    msg=update.message
    if msg is None:return
    if len(text.strip())<2:await msg.reply_text("Type a camera model, e.g. <code>Canon R7</code>.",parse_mode=ParseMode.HTML);return
    pending=await get_temp_data(user_id)
    try:camera=await identify_and_save_camera(user_id,text.strip())
    except Exception:logger.exception("camera_profile_failed user=%s",user_id);await msg.reply_text("⚠️ Camera profile could not be saved.");return
    await msg.reply_text(camera_profile_message(camera),parse_mode=ParseMode.HTML); nid=str(pending.get("photo_notification_id") or ""); photo_after=bool(pending.get("photo_after_camera")) or bool(nid); lens_after=bool(pending.get("lens_after_camera"))
    if lens_after:await set_user_state(user_id,UserState.WAITING_LENS,temp_data={});await msg.reply_text(_LENS_PROMPT,parse_mode=ParseMode.HTML);return
    await clear_user_state(user_id)
    if photo_after:await _send_recommendation(user_id,msg,notification_id=nid)

async def _handle_lens_text(update:Update,user_id:int,text:str)->None:
    msg=update.message
    if msg is None:return
    if len(text.strip())<2:await msg.reply_text("Type a lens, e.g. <code>RF 200-800mm</code>.",parse_mode=ParseMode.HTML);return
    try:lens=await identify_and_save_lens(user_id,text.strip())
    except Exception:logger.exception("lens_profile_failed user=%s",user_id);await msg.reply_text("⚠️ Lens profile could not be saved.");return
    await clear_user_state(user_id);await msg.reply_text(lens_profile_message(lens),parse_mode=ParseMode.HTML)

async def _send_recommendation(user_id:int,message,*,notification_id:str="")->None:
    progress=await message.reply_text("📡 Calculating trajectory + framing + atmosphere + deterministic camera settings…")
    try:
        rec,ctx=await recommend_for_user(user_id,notification_id=notification_id);await progress.edit_text(recommendation_message(rec,ctx),parse_mode=ParseMode.HTML,disable_web_page_preview=True)
    except MissingLocationError:await progress.edit_text("📍 Set your shooting location first with /location.")
    except MissingCameraError:await set_user_state(user_id,UserState.WAITING_CAMERA,temp_data={"photo_notification_id":notification_id});await progress.edit_text("📷 I need your camera body first.")
    except Exception:logger.exception("photo_recommendation_failed user=%s",user_id);await progress.edit_text("⚠️ The deterministic photography analysis failed. The live aircraft alert system continues running.")
