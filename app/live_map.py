"""Plane? v3.7 lightweight live relative-position map.

The map never creates another ADS-B polling loop. It reuses the existing bounded
trajectory history produced by the shared monitor and only falls back to the
notification snapshot already stored for Telegram alerts.
"""
from __future__ import annotations

import re
import time
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

from app.database import get_db, locations_col
from app.worker.monitor import get_aircraft_history

router = APIRouter()
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{6,64}$")
_SESSION_TTL_S = 600.0
_MAX_SESSIONS = 1024
_sessions: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()


def _valid_token(value: str) -> bool:
    return bool(_TOKEN_RE.fullmatch(value or ""))


def _epoch_ms(value: Any) -> int | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return int(value.timestamp() * 1000)
    try:
        return int(float(value) * 1000)
    except (TypeError, ValueError):
        return None


def _cache_get(token: str) -> dict[str, Any] | None:
    item = _sessions.get(token)
    if not item:
        return None
    expires_mono, value = item
    if time.monotonic() >= expires_mono:
        _sessions.pop(token, None)
        return None
    _sessions.move_to_end(token)
    return value


def _cache_put(token: str, value: dict[str, Any]) -> None:
    _sessions[token] = (time.monotonic() + _SESSION_TTL_S, value)
    _sessions.move_to_end(token)
    while len(_sessions) > _MAX_SESSIONS:
        _sessions.popitem(last=False)


async def _load_session(token: str) -> dict[str, Any]:
    cached = _cache_get(token)
    if cached:
        return cached

    snapshot = await get_db()["photo_alert_snapshots"].find_one({"_id": token})
    if not snapshot:
        raise HTTPException(status_code=404, detail="Live map link not found")

    expires_at = snapshot.get("expires_at")
    if isinstance(expires_at, datetime):
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at <= datetime.now(timezone.utc):
            raise HTTPException(status_code=410, detail="Live map link expired")

    user_lat = snapshot.get("observer_latitude")
    user_lon = snapshot.get("observer_longitude")
    if user_lat is None or user_lon is None:
        location = await locations_col().find_one(
            {"user_id": snapshot.get("user_id")},
            {"latitude": 1, "longitude": 1},
        )
        if location:
            user_lat = location.get("latitude")
            user_lon = location.get("longitude")

    if user_lat is None or user_lon is None:
        raise HTTPException(status_code=404, detail="Observer location unavailable")

    value = {
        "user_latitude": float(user_lat),
        "user_longitude": float(user_lon),
        "icao24": str(snapshot.get("aircraft_icao24") or "").lower(),
        "callsign": str(snapshot.get("callsign") or "").strip(),
        "aircraft_type": str(snapshot.get("aircraft_type") or "Aircraft").strip() or "Aircraft",
        "fallback": {
            "latitude": snapshot.get("latitude"),
            "longitude": snapshot.get("longitude"),
            "altitude_m": snapshot.get("altitude_m"),
            "speed_kts": (float(snapshot["speed_ms"]) * 1.9438444924406) if snapshot.get("speed_ms") is not None else None,
            "heading_deg": snapshot.get("heading_deg"),
            "position_age_s": snapshot.get("position_age_s"),
            "sample_epoch_ms": (_epoch_ms(snapshot.get("captured_at")) or int(time.time() * 1000))
            - int(float(snapshot.get("position_age_s") or 0.0) * 1000),
        },
        "expires_epoch_ms": _epoch_ms(expires_at),
    }
    _cache_put(token, value)
    return value


def _history_payload(icao24: str) -> list[dict[str, Any]]:
    samples = get_aircraft_history(icao24) if icao24 else []
    now = time.time()
    out: list[dict[str, Any]] = []
    for sample in samples[-24:]:
        if now - float(sample.timestamp) > 120.0:
            continue
        out.append(
            {
                "latitude": round(float(sample.latitude), 6),
                "longitude": round(float(sample.longitude), 6),
                "altitude_m": sample.altitude_m,
                "speed_kts": sample.speed_kts,
                "heading_deg": sample.heading_deg,
                "position_age_s": sample.position_age_s,
                "sample_epoch_ms": int(float(sample.timestamp) * 1000),
            }
        )
    return out


@router.get("/api/live/{notification_id}", include_in_schema=False)
async def live_map_data(notification_id: str) -> dict[str, Any]:
    if not _valid_token(notification_id):
        raise HTTPException(status_code=404, detail="Live map link not found")

    session = await _load_session(notification_id)
    trail = _history_payload(session["icao24"])
    aircraft = trail[-1] if trail else dict(session["fallback"])
    if aircraft.get("latitude") is None or aircraft.get("longitude") is None:
        raise HTTPException(status_code=404, detail="Aircraft position unavailable")

    return {
        "version": "3.7.0",
        "server_epoch_ms": int(time.time() * 1000),
        "poll_interval_ms": 2500,
        "background_poll_interval_ms": 15000,
        "observer": {
            "latitude": session["user_latitude"],
            "longitude": session["user_longitude"],
        },
        "aircraft": {
            **aircraft,
            "icao24": session["icao24"],
            "callsign": session["callsign"],
            "aircraft_type": session["aircraft_type"],
        },
        "trail": trail,
        "expires_epoch_ms": session["expires_epoch_ms"],
        "source": "shared ADS-B monitor cache",
    }


_PAGE = r'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#0b1118">
<title>Plane? Live Relative Map</title>
<link rel="preconnect" href="https://unpkg.com">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" crossorigin="">
<style>
:root{color-scheme:dark;font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
*{box-sizing:border-box}html,body{margin:0;min-height:100%;background:#0b1118;color:#f4f7fb}body{min-height:100dvh;display:grid;grid-template-rows:minmax(58dvh,1fr) auto}
#map{min-height:58dvh;background:#101923}.hud{background:#0d141d;border-top:1px solid #263240;padding:14px 16px max(16px,env(safe-area-inset-bottom));display:grid;gap:12px}
.topline{display:flex;align-items:center;justify-content:space-between;gap:12px}.brand{font-size:13px;font-weight:700;letter-spacing:.08em;text-transform:uppercase}.live{display:inline-flex;gap:7px;align-items:center;font-size:12px;color:#b8c4d2}.dot{width:8px;height:8px;border-radius:99px;background:#2dd36f;box-shadow:0 0 0 4px rgb(45 211 111/.12)}
.title{font-size:21px;font-weight:750;line-height:1.1}.subtitle{margin-top:3px;color:#9fb0c2;font-size:13px}.grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px}.metric{background:#121d28;border:1px solid #223141;border-radius:11px;padding:10px}.k{font-size:10px;color:#8396aa;text-transform:uppercase;letter-spacing:.08em}.v{margin-top:3px;font-size:15px;font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.actions{display:flex;gap:8px}.btn{appearance:none;border:1px solid #314255;background:#142131;color:#eef5fb;border-radius:10px;padding:9px 12px;font-weight:650}.note{font-size:11px;color:#7890a6;line-height:1.35}
.user-marker{width:18px;height:18px;border-radius:50%;background:#1689ff;border:3px solid #fff;box-shadow:0 0 0 5px rgb(22 137 255/.25),0 3px 12px #0008}.plane-marker{font-size:27px;line-height:27px;filter:drop-shadow(0 2px 4px #000c);transform-origin:50% 50%}
.leaflet-control-attribution{font-size:9px!important;background:#ffffffcc!important;color:#111!important}.leaflet-control-attribution a{color:#111!important}
@media(max-width:620px){body{grid-template-rows:minmax(55dvh,1fr) auto}.grid{grid-template-columns:repeat(2,minmax(0,1fr))}.hud{padding-left:12px;padding-right:12px}.title{font-size:19px}}
</style>
</head>
<body>
<div id="map"></div>
<section class="hud">
  <div class="topline"><span class="brand">Plane? · v3.7</span><span class="live"><i class="dot" id="liveDot"></i><span id="liveText">Connecting…</span></span></div>
  <div><div class="title" id="aircraftTitle">Live aircraft</div><div class="subtitle" id="aircraftSubtitle">Relative to your blue location marker</div></div>
  <div class="grid">
    <div class="metric"><div class="k">Distance</div><div class="v" id="distance">—</div></div>
    <div class="metric"><div class="k">Altitude</div><div class="v" id="altitude">—</div></div>
    <div class="metric"><div class="k">Speed</div><div class="v" id="speed">—</div></div>
    <div class="metric"><div class="k">Heading</div><div class="v" id="heading">—</div></div>
  </div>
  <div class="actions"><button class="btn" id="recenter">Recenter</button></div>
  <div class="note">Satellite tiles load directly in your browser. Plane? reuses the same shared ADS-B feed as alerts; this page does not start another aircraft-provider polling loop. The marker is smoothly extrapolated for only a few seconds between real ADS-B updates.</div>
</section>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js" crossorigin=""></script>
<script>
const token="__TOKEN__";
const map=L.map('map',{zoomControl:true,preferCanvas:true,attributionControl:true});
L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',{maxZoom:19,attribution:'Tiles © Esri'}).addTo(map);
let observer=null, plane=null, linkLine=null, trailLine=null, headingLine=null, latest=null, fitted=false, timer=null, stopped=false;
const $=id=>document.getElementById(id);
function rad(v){return v*Math.PI/180} function deg(v){return v*180/Math.PI}
function hav(a,b,c,d){const R=6371,dp=rad(c-a),dl=rad(d-b),q=Math.sin(dp/2)**2+Math.cos(rad(a))*Math.cos(rad(c))*Math.sin(dl/2)**2;return 2*R*Math.asin(Math.sqrt(q))}
function project(lat,lon,bearing,km){const R=6371,dr=km/R,br=rad(bearing||0),p1=rad(lat),l1=rad(lon),p2=Math.asin(Math.sin(p1)*Math.cos(dr)+Math.cos(p1)*Math.sin(dr)*Math.cos(br)),l2=l1+Math.atan2(Math.sin(br)*Math.sin(dr)*Math.cos(p1),Math.cos(dr)-Math.sin(p1)*Math.sin(p2));return[deg(p2),((deg(l2)+540)%360)-180]}
function planeIcon(h){return L.divIcon({className:'',iconSize:[30,30],iconAnchor:[15,15],html:`<div class="plane-marker" style="transform:rotate(${Number(h||0)}deg)">✈</div>`})}
function status(ok,text){$('liveText').textContent=text;$('liveDot').style.background=ok?'#2dd36f':'#ffb020'}
function fit(){if(!observer||!plane)return;map.fitBounds(L.latLngBounds([observer.getLatLng(),plane.getLatLng()]).pad(.35),{maxZoom:13,animate:false});fitted=true}
function updateHud(pos){if(!latest)return;const a=latest.aircraft,o=latest.observer;const d=hav(o.latitude,o.longitude,pos[0],pos[1]);$('distance').textContent=d.toFixed(1)+' km';$('altitude').textContent=a.altitude_m==null?'—':Math.round(a.altitude_m*3.28084).toLocaleString()+' ft';$('speed').textContent=a.speed_kts==null?'—':Math.round(a.speed_kts)+' kt';$('heading').textContent=a.heading_deg==null?'—':Math.round(a.heading_deg)+'°';$('aircraftTitle').textContent=(a.callsign? a.callsign+' · ':'')+(a.aircraft_type||'Aircraft');$('aircraftSubtitle').textContent=(a.icao24||'').toUpperCase()+' · relative to your blue location marker'}
function render(data){latest=data;const o=[data.observer.latitude,data.observer.longitude],a=[data.aircraft.latitude,data.aircraft.longitude];if(!observer)observer=L.marker(o,{icon:L.divIcon({className:'',iconSize:[18,18],iconAnchor:[9,9],html:'<div class="user-marker"></div>'}),zIndexOffset:900}).addTo(map);else observer.setLatLng(o);if(!plane)plane=L.marker(a,{icon:planeIcon(data.aircraft.heading_deg),zIndexOffset:1000}).addTo(map);else{plane.setLatLng(a);plane.setIcon(planeIcon(data.aircraft.heading_deg))}const pts=(data.trail||[]).map(p=>[p.latitude,p.longitude]);if(!trailLine)trailLine=L.polyline(pts,{weight:3,opacity:.8}).addTo(map);else trailLine.setLatLngs(pts);if(!linkLine)linkLine=L.polyline([o,a],{weight:2,opacity:.8,dashArray:'5 7'}).addTo(map);const future=project(a[0],a[1],data.aircraft.heading_deg||0,Math.max(.5,Math.min(18,(data.aircraft.speed_kts||0)*1.852/60*2)));if(!headingLine)headingLine=L.polyline([a,future],{weight:2,opacity:.65,dashArray:'9 8'}).addTo(map);else headingLine.setLatLngs([a,future]);updateHud(a);if(!fitted)fit();status(true,'Live · '+Math.round((Date.now()-data.aircraft.sample_epoch_ms)/1000)+'s old')}
async function poll(){if(stopped)return;try{const r=await fetch('/api/live/'+encodeURIComponent(token),{cache:'no-store'});if(!r.ok)throw new Error('HTTP '+r.status);render(await r.json())}catch(e){status(false,'Waiting for data')}finally{clearTimeout(timer);timer=setTimeout(poll,document.hidden?15000:2500)}}
function animate(){if(latest&&plane&&observer){const a=latest.aircraft,age=Math.max(0,(Date.now()-Number(a.sample_epoch_ms||Date.now()))/1000),dt=Math.min(age,7);let pos=[a.latitude,a.longitude];if(a.speed_kts&&a.heading_deg!=null&&dt>0){pos=project(a.latitude,a.longitude,a.heading_deg,a.speed_kts*1.852*dt/3600)}plane.setLatLng(pos);if(linkLine)linkLine.setLatLngs([[latest.observer.latitude,latest.observer.longitude],pos]);updateHud(pos);if(age>10)status(false,'ADS-B update delayed')}requestAnimationFrame(animate)}
$('recenter').addEventListener('click',fit);document.addEventListener('visibilitychange',()=>{clearTimeout(timer);poll()});window.addEventListener('beforeunload',()=>{stopped=true;clearTimeout(timer)});poll();requestAnimationFrame(animate);
</script>
</body></html>'''


@router.get("/live/{notification_id}", response_class=HTMLResponse, include_in_schema=False)
async def live_map_page(notification_id: str) -> HTMLResponse:
    if not _valid_token(notification_id):
        raise HTTPException(status_code=404, detail="Live map link not found")
    return HTMLResponse(
        _PAGE.replace("__TOKEN__", notification_id),
        headers={"Cache-Control": "private, no-store", "X-Robots-Tag": "noindex, nofollow"},
    )
