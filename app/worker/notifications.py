"""Telegram notifications with v3.4 live approach message updates."""
from __future__ import annotations
import asyncio,logging
from datetime import datetime,timedelta,timezone
from html import escape
from urllib.parse import quote
from telegram import Bot,InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.error import BadRequest,Forbidden,TelegramError
from app.aircraft.models import NormalizedAircraft
from app.bot.messages import aircraft_alert_message
from app.config import settings
from app.database import get_db,users_col
from app.photography.keyboards import notification_actions_keyboard
logger=logging.getLogger(__name__);_send_semaphore=asyncio.Semaphore(20);_MIN_SEND_INTERVAL=.05;_bot_instance:Bot|None=None

def _safe(v:str)->str:return escape(v or "",quote=True)
def _safe_provider_text(value:str)->str:
    """Backward-compatible provider escaping helper retained for existing callers/tests."""
    return _safe(value)
def _get_bot()->Bot:
    global _bot_instance
    if _bot_instance is None or _bot_instance.token!=settings.telegram_bot_token:_bot_instance=Bot(token=settings.telegram_bot_token)
    return _bot_instance
def _clock(seconds:float|None)->str:
    if seconds is None:return "—"
    s=max(0,int(round(seconds)));return f"{s//60:02d}:{s%60:02d}"

def _approach_text(ac,pred,stage,camera=None,environment=None,previous_cpa_km=None,observed_closest_km=None,prediction_changed=False)->str:
    title={"prepare":"📡 <b>NEXT SHOT</b>","camera_ready":"📷 <b>CAMERA READY</b>","photo_now":"🔥 <b>PHOTO NOW</b>","passed":"✅ <b>AIRCRAFT PASSED</b>","cancelled":"↪️ <b>TRAJECTORY CHANGED</b>"}.get(stage,"✈️ <b>SPOTTING</b>")
    lines=[title,f"\n<b>{_safe(ac.callsign) or _safe(ac.aircraft_type) or 'Aircraft'}</b> · <code>{_safe(ac.aircraft_type or 'Unknown')}</code>",f"ICAO: <code>{_safe(ac.icao24)}</code>"]
    if prediction_changed and stage not in {"cancelled","passed"}:lines.append("\n🔄 <b>Prediction changed</b> — recalculated from newer ADS-B trajectory data.")
    if stage=="cancelled":
        lines += ["\nApproach alert cancelled.",f"New closest projected pass: <b>{pred.projected_closest_km:.1f} km</b>"]
        if previous_cpa_km is not None:lines.append(f"Previous prediction: {float(previous_cpa_km):.1f} km")
        return "\n".join(lines)
    if stage!="passed":
        countdown=camera.best_window_start_s if camera and camera.best_window_start_s is not None else pred.time_to_cpa_s;lines.append(f"\n<b>{_clock(countdown)}</b> until best shooting window")
    if stage=="passed" and observed_closest_km is not None:lines.append(f"Closest observed distance: <b>{float(observed_closest_km):.1f} km</b>")
    lines += [f"Closest projected pass: <b>{pred.projected_closest_km:.1f} km</b>",f"Trajectory: <b>{escape(pred.state)}</b>",f"Confidence: <b>{escape(pred.confidence)}</b>",f"Current distance: {pred.current_distance_km:.1f} km"]
    if pred.projected_closest_slant_km is not None:lines.append(f"Closest slant distance: ~{pred.projected_closest_slant_km:.1f} km")
    if ac.altitude is not None:lines.append(f"Altitude: {int(round(ac.altitude*3.28084)):,} ft")
    if ac.ground_speed is not None:lines.append(f"Speed: {int(round(ac.ground_speed))} kt")
    if camera:
        lines += ["\n<b>CAMERA</b>",f"{camera.shutter_speed} · {camera.aperture} · {escape(camera.iso)}",f"Focal length: <b>{escape(camera.focal_length)}</b> ({camera.focal_range_mm[0]}–{camera.focal_range_mm[1]} mm)"]
        if camera.frame_fill_pct is not None:lines.append(f"Frame fill: ~{camera.frame_fill_pct:.0f}% · clipping risk {escape(camera.clipping_risk)}")
        if camera.angular_speed_deg_s is not None:lines.append(f"Angular motion: ~{camera.angular_speed_deg_s:.2f}°/s")
        if camera.dynamic_focal:lines.append("Focal plan: "+" · ".join(f"{t}s→{f}mm" for t,f in camera.dynamic_focal[:4]))
    if environment:
        solar=environment.get("solar");atm=environment.get("atmosphere");con=environment.get("contrail");lines.append("\n<b>CONDITIONS</b>")
        if solar:lines.append(f"Light: {escape(solar.lighting_relationship)} · Sun {solar.elevation_deg if solar.elevation_deg is not None else '—'}°")
        if atm:lines.append(f"Heat haze: {escape(atm.heat_haze)} (estimated) · clarity: {atm.clarity_score}/100 ({escape(atm.clarity_label)})")
        if con:
            lines.append(f"Contrail formation: {escape(con.formation)}");lines.append(f"Persistence: {escape(con.persistence)} · confidence {escape(con.confidence)}")
            if con.reasons:lines.append("Contrail basis: "+escape("; ".join(con.reasons[:2])))
        elif environment.get("upper_error"):lines.append("Contrail: limited upper-air data · confidence Low")
        sc=environment.get("sun_crossing");mc=environment.get("moon_crossing")
        if sc and sc.candidate:lines += [f"☀️ Potential solar crossing: {sc.min_separation_deg:.2f}° in ~{int(sc.time_to_min_s or 0)}s",f"⚠️ {escape(sc.safety_warning or '')}"]
        if mc and mc.candidate:lines.append(f"🌙 Moon crossing candidate: {mc.min_separation_deg:.2f}° in ~{int(mc.time_to_min_s or 0)}s")
    lines.append(f"\n<a href=\"https://globe.adsb.fi/?icao={quote(ac.icao24 or '',safe='')}\">Live aircraft map</a>");return "\n".join(lines)

async def _record_photo_snapshot(user_id:int,aircraft:NormalizedAircraft,distance_km:float,notification_id:str,eta_seconds:float|None)->None:
    if not notification_id:return
    now=datetime.now(timezone.utc);await get_db()["photo_alert_snapshots"].update_one({"_id":notification_id,"user_id":user_id},{"$set":{"user_id":user_id,"aircraft_icao24":aircraft.icao24 or "","aircraft_type":aircraft.aircraft_type or aircraft.display_type or "","callsign":aircraft.callsign or "","distance_km":float(distance_km),"altitude_m":aircraft.altitude,"speed_ms":aircraft.velocity,"heading_deg":aircraft.heading,"vertical_rate_mps":getattr(aircraft,"vertical_rate_mps",None),"position_age_s":getattr(aircraft,"position_age_s",None),"latitude":aircraft.latitude,"longitude":aircraft.longitude,"eta_seconds":eta_seconds,"captured_at":now,"expires_at":now+timedelta(hours=6)}},upsert=True)

async def send_or_update_approach(user_id:int,aircraft,prediction,stage:str,notification_id:str,message_id:int|None=None,*,camera=None,environment=None,previous_cpa_km=None,observed_closest_km=None,prediction_changed=False)->int|None:
    text=_approach_text(aircraft,prediction,stage,camera,environment,previous_cpa_km,observed_closest_km,prediction_changed);markup=notification_actions_keyboard(notification_id) if notification_id else None
    if notification_id:
        try:
            await _record_photo_snapshot(user_id,aircraft,prediction.current_distance_km,notification_id,prediction.time_to_cpa_s);now=datetime.now(timezone.utc)
            await get_db()["notification_history"].update_one({"_id":notification_id},{"$set":{"user_id":user_id,"aircraft_icao24":aircraft.icao24,"aircraft_type":aircraft.aircraft_type,"distance_km":prediction.current_distance_km,"projected_closest_km":prediction.projected_closest_km,"observed_closest_km":observed_closest_km,"trajectory_state":prediction.state,"prediction_confidence":prediction.confidence,"notified_at":now,"cooldown_until":now+timedelta(minutes=settings.cooldown_minutes)}},upsert=True)
        except Exception:logger.exception("approach_snapshot_failed user=%s icao=%s",user_id,aircraft.icao24)
    async with _send_semaphore:
        try:
            bot=_get_bot()
            if message_id:
                try:
                    await bot.edit_message_text(chat_id=user_id,message_id=int(message_id),text=text,parse_mode=ParseMode.HTML,disable_web_page_preview=True,reply_markup=markup);return int(message_id)
                except BadRequest as exc:
                    if "message is not modified" in str(exc).lower():return int(message_id)
                    logger.info("live_edit_failed user=%s message=%s error=%s",user_id,message_id,type(exc).__name__)
            sent=await bot.send_message(chat_id=user_id,text=text,parse_mode=ParseMode.HTML,disable_web_page_preview=True,reply_markup=markup);await asyncio.sleep(_MIN_SEND_INTERVAL);return int(sent.message_id)
        except Forbidden:await users_col().update_one({"user_id":user_id},{"$set":{"setup_complete":False}});return None
        except TelegramError as exc:logger.error("approach_message_failed user=%s error=%s",user_id,type(exc).__name__);return None
        except Exception:logger.exception("approach_message_unexpected user=%s",user_id);return None

async def send_aircraft_notification(user_id:int,aircraft:NormalizedAircraft,distance_km:float,notification_id:str="",eta_seconds:float|None=None)->bool:
    msg=aircraft_alert_message(aircraft_type=_safe(aircraft.display_type),callsign=_safe(aircraft.callsign),distance_km=distance_km,altitude_m=aircraft.altitude,velocity_ms=aircraft.velocity,heading=aircraft.heading,icao24=quote(aircraft.icao24 or "",safe=""),origin_country=_safe(aircraft.origin_country),eta_seconds=eta_seconds)
    if notification_id:
        try:await _record_photo_snapshot(user_id,aircraft,distance_km,notification_id,eta_seconds)
        except Exception:logger.exception("Could not persist photo snapshot %s",notification_id)
    return await _send_message(user_id,msg,notification_actions_keyboard(notification_id) if notification_id else None)

async def _send_message(user_id:int,text:str,reply_markup:InlineKeyboardMarkup|None=None)->bool:
    async with _send_semaphore:
        try:await _get_bot().send_message(chat_id=user_id,text=text,parse_mode=ParseMode.HTML,disable_web_page_preview=True,reply_markup=reply_markup);await asyncio.sleep(_MIN_SEND_INTERVAL);return True
        except Forbidden:await users_col().update_one({"user_id":user_id},{"$set":{"setup_complete":False}});return False
        except Exception:logger.exception("notification_failed user=%s",user_id);return False

async def send_admin_alert(text:str)->None:
    if not settings.admin_telegram_id:return
    try:await _get_bot().send_message(chat_id=settings.admin_telegram_id,text=f"🔔 <b>Admin Alert</b>\n\n{escape(text,quote=True)}",parse_mode=ParseMode.HTML)
    except Exception:logger.exception("Failed to send admin alert")
