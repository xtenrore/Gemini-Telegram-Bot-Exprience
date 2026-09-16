"""Plane? v3.4 photography orchestration.

Physical/photographic calculations are deterministic. Gemini, when available,
only explains the already-calculated recommendation and is never required.
"""
from __future__ import annotations
import asyncio, time
from datetime import datetime, timezone
from typing import Any
from app.aircraft.categories import AIRCRAFT_CATEGORIES, resolve_match_prefixes
from app.config import settings
from app.database import get_db, locations_col, notification_history_col, preferences_col
from app.intelligence.advisor import explain as explain_with_gemini
from app.intelligence.camera import recommend_camera
from app.intelligence.environment import estimate_atmosphere, estimate_contrail, interpolate_flight_level
from app.intelligence.profiles import resolve_camera_profile, resolve_lens_profile
from app.intelligence.trajectory import HistorySample, predict_trajectory
from app.intelligence.upper_air import get_upper_air_profile
from app.photography.conditions import get_current_conditions
from app.photography.models import AircraftPhotoContext, CameraProfile, CameraSettings, LensProfile, PhotoRecommendation, PhotographyContext, WeatherContext
from app.photography.solar import get_solar_context
from app.worker.geo import haversine, km_to_nautical_miles

class PhotographySetupError(RuntimeError):pass
class MissingLocationError(PhotographySetupError):pass
class MissingCameraError(PhotographySetupError):pass

def _profiles_col():return get_db()["camera_profiles"]

async def get_camera_setup(user_id:int)->tuple[CameraProfile|None,LensProfile|None]:
    doc=await _profiles_col().find_one({"user_id":user_id})
    if not doc:return None,None
    try:camera=CameraProfile.model_validate(doc.get("camera")) if doc.get("camera") else None
    except Exception:camera=None
    try:lens=LensProfile.model_validate(doc.get("lens")) if doc.get("lens") else None
    except Exception:lens=None
    return camera,lens

async def identify_and_save_camera(user_id:int,user_text:str)->CameraProfile:
    profile=CameraProfile.model_validate(resolve_camera_profile(user_text)); now=datetime.now(timezone.utc)
    await _profiles_col().update_one({"user_id":user_id},{"$set":{"user_id":user_id,"camera":profile.model_dump(mode="json"),"camera_updated_at":now},"$setOnInsert":{"created_at":now}},upsert=True); return profile

async def identify_and_save_lens(user_id:int,user_text:str)->LensProfile:
    profile=LensProfile.model_validate(resolve_lens_profile(user_text)); now=datetime.now(timezone.utc)
    await _profiles_col().update_one({"user_id":user_id},{"$set":{"user_id":user_id,"lens":profile.model_dump(mode="json"),"lens_updated_at":now},"$setOnInsert":{"created_at":now}},upsert=True); return profile

def _watched_prefixes(prefs:dict[str,Any]|None)->set[str]:
    if not prefs:return set()
    base=set(prefs.get("custom_aircraft",[])); disabled=set(prefs.get("disabled_types",[]))
    for category in prefs.get("selected_categories",[]):base.update(t for t in AIRCRAFT_CATEGORIES.get(category,[]) if t not in disabled)
    return resolve_match_prefixes(base)

async def _find_live_aircraft(latitude:float,longitude:float,radius_km:float,*,target_icao24:str="",prefs:dict[str,Any]|None=None)->AircraftPhotoContext|None:
    from app.worker.monitor import get_provider_manager
    aircraft,_=await get_provider_manager().query_providers(latitude=latitude,longitude=longitude,radius_nm=min(250,max(60,int(km_to_nautical_miles(radius_km+80)))),provider_names=None)
    target=target_icao24.lower().strip(); prefixes=_watched_prefixes(prefs); candidates=[]
    for ac in aircraft:
        if not ac.has_position:continue
        if target and (ac.icao24 or "").lower()!=target:continue
        if not target and prefixes:
            typ=(ac.aircraft_type or "").upper().strip()
            if not typ or not any(typ.startswith(p) for p in prefixes):continue
        distance=haversine(latitude,longitude,ac.latitude,ac.longitude)
        if target or distance<=radius_km+60:candidates.append((distance,ac))
    if not candidates:return None
    distance,ac=min(candidates,key=lambda x:x[0])
    return AircraftPhotoContext(icao24=ac.icao24 or "",aircraft_type=ac.aircraft_type or ac.display_type or "",callsign=ac.callsign or "",distance_km=round(distance,2),altitude_m=ac.altitude,speed_ms=ac.velocity,heading_deg=ac.heading,vertical_rate_mps=getattr(ac,"vertical_rate_mps",None),position_age_s=getattr(ac,"position_age_s",None),latitude=ac.latitude,longitude=ac.longitude,live=True)

def _aircraft_from_doc(doc:dict[str,Any])->AircraftPhotoContext:
    def num(name):return float(doc[name]) if doc.get(name) is not None else None
    return AircraftPhotoContext(icao24=str(doc.get("aircraft_icao24") or ""),aircraft_type=str(doc.get("aircraft_type") or ""),callsign=str(doc.get("callsign") or ""),distance_km=num("distance_km"),altitude_m=num("altitude_m"),speed_ms=num("speed_ms"),heading_deg=num("heading_deg"),vertical_rate_mps=num("vertical_rate_mps"),position_age_s=num("position_age_s"),latitude=num("latitude"),longitude=num("longitude"),eta_seconds=num("eta_seconds"),live=False)

async def _notification_fallback(user_id:int,notification_id:str)->AircraftPhotoContext|None:
    if not notification_id:return None
    snapshot=await get_db()["photo_alert_snapshots"].find_one({"_id":notification_id,"user_id":user_id})
    if snapshot:return _aircraft_from_doc(snapshot)
    doc=await notification_history_col().find_one({"_id":notification_id,"user_id":user_id}); return _aircraft_from_doc(doc) if doc else None

async def build_photography_context(user_id:int,*,notification_id:str="")->PhotographyContext:
    location=await locations_col().find_one({"user_id":user_id})
    if not location:raise MissingLocationError("Set your monitoring location first with /location.")
    camera,lens=await get_camera_setup(user_id)
    if camera is None:raise MissingCameraError("Camera profile is not configured.")
    latitude,longitude=float(location["latitude"]),float(location["longitude"]); radius_km=float(location.get("radius_km",settings.default_radius_km)); prefs=await preferences_col().find_one({"user_id":user_id})
    fallback=await _notification_fallback(user_id,notification_id) if notification_id else None; target=fallback.icao24 if fallback else ""
    weather_task=asyncio.create_task(get_current_conditions(latitude,longitude)); live_task=asyncio.create_task(_find_live_aircraft(latitude,longitude,radius_km,target_icao24=target,prefs=prefs)); weather=await weather_task
    try:live=await asyncio.wait_for(asyncio.shield(live_task),timeout=1.5)
    except Exception:live=None; live_task.cancel()
    if live and fallback:
        live.eta_seconds=fallback.eta_seconds; live.callsign=live.callsign or fallback.callsign; live.aircraft_type=live.aircraft_type or fallback.aircraft_type
    aircraft=live or fallback; solar=get_solar_context(latitude,longitude,weather.timezone,subject_latitude=aircraft.latitude if aircraft else None,subject_longitude=aircraft.longitude if aircraft else None)
    return PhotographyContext(latitude=latitude,longitude=longitude,camera=camera,lens=lens,weather=weather,solar=solar,aircraft=aircraft)

def _trajectory_samples(context:PhotographyContext)->list[HistorySample]:
    ac=context.aircraft
    if not ac or ac.latitude is None or ac.longitude is None:return []
    try:
        from app.worker.monitor import get_aircraft_history
        history=get_aircraft_history(ac.icao24)
        if history:return history
    except Exception:pass
    now=time.time(); return [HistorySample(now-float(ac.position_age_s or 0.0),ac.latitude,ac.longitude,ac.altitude_m,(ac.speed_ms*1.9438444924406) if ac.speed_ms is not None else None,ac.heading_deg,ac.vertical_rate_mps,float(ac.position_age_s or 0.0))]

def _spot(prefs:dict[str,Any]|None)->dict[str,Any]:
    raw=(prefs or {}).get("spotting") or {}; return {"mode":str(raw.get("mode") or "Standard aviation"),"max_auto_iso":int(raw.get("max_auto_iso") or 1600)}

async def recommend_for_user(user_id:int,*,notification_id:str="")->tuple[PhotoRecommendation,PhotographyContext]:
    context=await build_photography_context(user_id,notification_id=notification_id); prefs=await preferences_col().find_one({"user_id":user_id}); location=await locations_col().find_one({"user_id":user_id}); spot=_spot(prefs); radius=float((location or {}).get("radius_km",settings.default_radius_km)); ac=context.aircraft; samples=_trajectory_samples(context)
    if ac and samples:pred=predict_trajectory(samples,context.latitude,context.longitude,radius,now=time.time())
    else:
        class P:path=[];time_to_cpa_s=None;confidence="Low";confidence_score=.35;projected_closest_km=radius;current_distance_km=radius
        pred=P()
    w=context.weather; atmosphere=estimate_atmosphere(temperature_c=w.temperature_c,surface_temperature_c=w.soil_temperature_0cm_c,humidity_pct=w.relative_humidity_pct,visibility_m=w.visibility_m,wind_kmh=w.wind_speed_kmh,solar_wm2=w.shortwave_radiation_wm2,sun_elevation_deg=context.solar.elevation_deg,distance_km=float(getattr(pred,"current_distance_km",radius)),pm25=w.pm2_5_ugm3,aerosol_optical_depth=w.aerosol_optical_depth,precipitation_mm=w.precipitation_mm)
    contrail=None
    if ac and ac.altitude_m is not None and ac.latitude is not None and ac.longitude is not None:
        layers,_=await get_upper_air_profile(ac.latitude,ac.longitude,ac.altitude_m); contrail=estimate_contrail(interpolate_flight_level(layers,ac.altitude_m),ac.aircraft_type)
    camera=recommend_camera(camera=context.camera,lens=context.lens,aircraft_type=ac.aircraft_type if ac else "",prediction=pred,observer_lat=context.latitude,observer_lon=context.longitude,mode=spot["mode"],max_auto_iso=spot["max_auto_iso"],light_relationship=context.solar.lighting_relationship,sun_elevation_deg=context.solar.elevation_deg,heat_haze_level=atmosphere.heat_haze)
    score=float(getattr(pred,"confidence_score",.35)); confidence="high" if score>=.78 else "medium" if score>=.50 else "low"; light=context.solar.lighting_relationship; quality=int(max(10,min(98,atmosphere.clarity_score-atmosphere.heat_haze_score*.25+(8 if "side" in light.lower() else -8 if "back" in light.lower() else 0))))
    timing="Use this as a baseline until a matching aircraft is tracked."
    if camera.best_window_start_s is not None and camera.best_window_end_s is not None:timing=f"Best predicted shooting window starts in ~{max(0,int(camera.best_window_start_s))}s and lasts to ~{max(0,int(camera.best_window_end_s))}s."
    reasons=[f"Trajectory confidence: {getattr(pred,'confidence','Low')}; projected closest pass {getattr(pred,'projected_closest_km',radius):.1f} km.",f"Angular-motion-derived shutter floor: {camera.shutter_floor}.",f"Estimated atmospheric clarity {atmosphere.clarity_score}/100; heat haze {atmosphere.heat_haze}."]
    if camera.frame_fill_pct is not None:reasons[1]+=f" Frame fill approximately {camera.frame_fill_pct:.0f}%."
    if contrail:reasons.append(f"Contrail formation {contrail.formation}; persistence {contrail.persistence}; confidence {contrail.confidence}.")
    warnings=list(camera.notes)
    if "back" in light.lower():warnings.append("Backlighting may reduce aircraft detail and increase highlight contrast.")
    payload={"camera":f"{context.camera.brand} {context.camera.model}".strip(),"lens":context.lens.model if context.lens else "unknown","aircraft_category":ac.aircraft_type if ac else "unknown","distance_band":round(getattr(pred,"projected_closest_km",radius)/2)*2,"lighting":light,"heat_haze":atmosphere.heat_haze,"clarity_band":atmosphere.clarity_label,"contrail":contrail.formation if contrail else "unknown","framing_band":round(camera.frame_fill_pct/10)*10 if camera.frame_fill_pct is not None else None,"mode":spot["mode"],"settings":{"shutter":camera.shutter_speed,"aperture":camera.aperture,"iso":camera.iso,"focal":camera.focal_length}}
    try:ai_text=await asyncio.wait_for(explain_with_gemini(payload,timeout_s=1.5),timeout=1.8)
    except Exception:ai_text=None
    summary=f"Use {camera.shutter_speed}, {camera.aperture}, {camera.iso}; start around {camera.focal_length}."+(" "+ai_text if ai_text else "")
    rec=PhotoRecommendation(title="Plane? Spotting Intelligence",quality_score=quality,confidence=confidence,summary=summary,light_assessment=light,atmosphere_assessment=f"Estimated clarity {atmosphere.clarity_score}/100 ({atmosphere.clarity_label})",heat_haze_assessment=f"Estimated {atmosphere.heat_haze}",settings=CameraSettings(exposure_mode="M + Auto ISO",shutter_speed=camera.shutter_speed,aperture=camera.aperture,iso=camera.iso,exposure_compensation=camera.exposure_compensation,autofocus_mode="Continuous/Servo AF",autofocus_area="Subject/aircraft tracking",drive_mode=camera.burst,stabilization=camera.stabilization,focal_length=camera.focal_length,metering="Evaluative/matrix",white_balance="Auto WB",file_format="RAW"),technique=[camera.autofocus,camera.burst,f"Dynamic focal guidance: {camera.dynamic_focal[:4]}" if camera.dynamic_focal else "Track smoothly and leave framing margin."],warnings=warnings,best_timing=timing,reasoning=reasons,model_used="deterministic-v3.4 + Gemini advisor" if ai_text else "deterministic-v3.4")
    return rec,context

async def get_conditions_for_user(user_id:int)->tuple[WeatherContext,Any]:
    location=await locations_col().find_one({"user_id":user_id})
    if not location:raise MissingLocationError("Set your monitoring location first with /location.")
    latitude,longitude=float(location["latitude"]),float(location["longitude"]); weather=await get_current_conditions(latitude,longitude); solar=get_solar_context(latitude,longitude,weather.timezone); return weather,solar
