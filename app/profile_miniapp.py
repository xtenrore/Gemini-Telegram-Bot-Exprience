"""Telegram Mini App Visual Setup for Plane Alerts v4.4.

Visual Setup and Guided Setup both read/write ``profiles`` through
``app.alert_profiles``. There is no second settings database.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from fastapi import APIRouter, Request, status
from fastapi.responses import HTMLResponse, JSONResponse

from app.aircraft.filtering import normalized_filter_config, normalized_rule_config
from app.aircraft.registry import AIRCRAFT_TYPES, CATEGORY_LABELS, CATEGORY_ORDER, category_members, search_aircraft
from app.alert_profiles import blank_config_from, create_profile, get_active_profile, get_profile, list_profiles, save_profile
from app.bot.next60_web import validate_telegram_init_data
from app.config import settings
from app.intelligence.profiles import resolve_camera_profile, resolve_lens_profile

router = APIRouter()
_ALLOWED_RULE_KEYS = {"enabled", "min_altitude_ft", "max_altitude_ft", "airline_mode", "airlines", "radius_km"}


def _auth(payload: Any) -> int | None:
    if not isinstance(payload, dict):
        return None
    return validate_telegram_init_data(str(payload.get("init_data") or ""), settings.telegram_bot_token)


def _json_error(detail: str, code: int) -> JSONResponse:
    return JSONResponse({"detail": detail}, status_code=code, headers={"Cache-Control": "no-store"})


def _camera_label(raw: dict[str, Any]) -> dict[str, Any]:
    camera = dict(raw.get("camera") or {})
    lens = dict(raw.get("lens") or {})
    return {
        "camera_text": " ".join(str(camera.get(k) or "") for k in ("brand", "model")).strip() or str(camera.get("raw_input") or ""),
        "lens_text": " ".join(str(lens.get(k) or "") for k in ("brand", "model")).strip() or str(lens.get("raw_input") or ""),
    }


def _serialize_profile(profile: dict[str, Any]) -> dict[str, Any]:
    cfg = deepcopy(profile.get("config") or {})
    return {
        "profile_id": str(profile.get("profile_id") or ""),
        "name": str(profile.get("name") or "Profile"),
        "config": cfg,
        "camera_labels": _camera_label(dict(cfg.get("camera") or {})),
    }


def _number(value: Any, lo: float, hi: float, *, allow_none: bool = True) -> float | None:
    if value in (None, "") and allow_none:
        return None
    number = float(value)
    if not lo <= number <= hi:
        raise ValueError("number out of range")
    return number


def _clean_rule(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, Any] = {}
    for key, value in raw.items():
        if key not in _ALLOWED_RULE_KEYS:
            continue
        if key == "enabled":
            if value is not None:
                out[key] = bool(value)
        elif key in {"min_altitude_ft", "max_altitude_ft"}:
            out[key] = _number(value, 0, 100000)
        elif key == "radius_km":
            if value not in (None, ""):
                out[key] = _number(value, 1, 150, allow_none=False)
        elif key == "airline_mode":
            mode = str(value or "all").lower()
            if mode not in {"all", "whitelist", "blacklist"}:
                raise ValueError("invalid airline mode")
            out[key] = mode
        elif key == "airlines":
            if not isinstance(value, list):
                raise ValueError("airlines must be a list")
            out[key] = sorted({str(item).strip().upper()[:8] for item in value if str(item).strip()})[:80]
    minimum, maximum = out.get("min_altitude_ft"), out.get("max_altitude_ft")
    if minimum is not None and maximum is not None and minimum > maximum:
        raise ValueError("minimum altitude exceeds maximum")
    return out


def _clean_config(raw: Any, existing: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("config must be an object")
    out = deepcopy(existing)

    loc_raw = raw.get("location") if isinstance(raw.get("location"), dict) else {}
    lat = _number(loc_raw.get("latitude"), -90, 90)
    lon = _number(loc_raw.get("longitude"), -180, 180)
    radius = _number(loc_raw.get("radius_km", settings.default_radius_km), 1, 150, allow_none=False)
    out["location"] = {"latitude": lat, "longitude": lon, "radius_km": radius}
    if loc_raw.get("geohash"):
        out["location"]["geohash"] = str(loc_raw.get("geohash"))[:24]

    prefs_existing = dict(out.get("preferences") or {})
    prefs_raw = raw.get("preferences") if isinstance(raw.get("preferences"), dict) else {}
    prefs_existing.update({k: deepcopy(v) for k, v in prefs_raw.items() if k not in {"aircraft_filter", "filter_rules"}})
    selection = normalized_filter_config(prefs_raw)
    selection["selected_categories"] = [value for value in selection["selected_categories"] if value in CATEGORY_LABELS]
    selection["selected_types"] = [value for value in selection["selected_types"] if value in AIRCRAFT_TYPES]
    selection["excluded_types"] = [value for value in selection["excluded_types"] if value in AIRCRAFT_TYPES]
    prefs_existing["aircraft_filter"] = selection

    rules = normalized_rule_config(prefs_raw)
    prefs_existing["filter_rules"] = {
        "profile": _clean_rule(rules.get("profile")),
        "categories": {key: _clean_rule(value) for key, value in rules.get("categories", {}).items() if key in CATEGORY_LABELS},
        "aircraft": {key: _clean_rule(value) for key, value in rules.get("aircraft", {}).items() if key in AIRCRAFT_TYPES},
    }
    out["preferences"] = prefs_existing

    camera_raw = raw.get("camera") if isinstance(raw.get("camera"), dict) else dict(out.get("camera") or {})
    camera_text = str(camera_raw.pop("camera_text", "") or "").strip()
    lens_text = str(camera_raw.pop("lens_text", "") or "").strip()
    if camera_text:
        camera_raw["camera"] = resolve_camera_profile(camera_text)
    if lens_text:
        camera_raw["lens"] = resolve_lens_profile(lens_text)
    out["camera"] = camera_raw
    return out


@router.get("/profile-setup-ui", response_class=HTMLResponse)
async def profile_setup_ui() -> HTMLResponse:
    return HTMLResponse(PROFILE_SETUP_HTML, headers={"Cache-Control": "no-store"})


@router.post("/api/profile-setup/bootstrap")
async def profile_setup_bootstrap(request: Request) -> JSONResponse:
    try:
        payload = await request.json()
    except Exception:
        return _json_error("invalid request", status.HTTP_400_BAD_REQUEST)
    user_id = _auth(payload)
    if user_id is None:
        return _json_error("unauthorized", status.HTTP_401_UNAUTHORIZED)
    active = await get_active_profile(user_id)
    profiles = await list_profiles(user_id)
    requested = str(payload.get("profile_id") or "")
    selected = await get_profile(user_id, requested) if requested else active
    if selected is None:
        selected = active
    if selected is None:
        return _json_error("profile unavailable", status.HTTP_404_NOT_FOUND)
    return JSONResponse({
        "active_profile_id": str((active or {}).get("profile_id") or ""),
        "profiles": [{"profile_id": str(p.get("profile_id")), "name": str(p.get("name") or "Profile")} for p in profiles],
        "profile": _serialize_profile(selected),
        "categories": [{"key": key, "label": CATEGORY_LABELS[key], "count": len(category_members(key))} for key in CATEGORY_ORDER],
    }, headers={"Cache-Control": "no-store"})


@router.post("/api/profile-setup/catalogue")
async def profile_setup_catalogue(request: Request) -> JSONResponse:
    try:
        payload = await request.json()
    except Exception:
        return _json_error("invalid request", status.HTTP_400_BAD_REQUEST)
    if _auth(payload) is None:
        return _json_error("unauthorized", status.HTTP_401_UNAUTHORIZED)
    query = str(payload.get("query") or "").strip()[:80]
    category = str(payload.get("category") or "").strip()
    page = max(0, min(int(payload.get("page") or 0), 200))
    page_size = 40
    if query:
        rows = search_aircraft(query, limit=400)
        if category in CATEGORY_LABELS:
            rows = [row for row in rows if category in row.categories]
    elif category in CATEGORY_LABELS:
        rows = category_members(category)
    else:
        rows = sorted(AIRCRAFT_TYPES.values(), key=lambda item: (item.manufacturer, item.model, item.code))
    start = page * page_size
    items = rows[start:start + page_size]
    return JSONResponse({
        "items": [{
            "code": row.code,
            "manufacturer": row.manufacturer,
            "model": row.model,
            "family": row.family,
            "categories": list(row.categories),
        } for row in items],
        "page": page,
        "has_more": start + page_size < len(rows),
        "total": len(rows),
    }, headers={"Cache-Control": "no-store"})


@router.post("/api/profile-setup/create")
async def profile_setup_create(request: Request) -> JSONResponse:
    try:
        payload = await request.json()
    except Exception:
        return _json_error("invalid request", status.HTTP_400_BAD_REQUEST)
    user_id = _auth(payload)
    if user_id is None:
        return _json_error("unauthorized", status.HTTP_401_UNAUTHORIZED)
    active = await get_active_profile(user_id)
    if active is None:
        return _json_error("profile unavailable", status.HTTP_404_NOT_FOUND)
    try:
        created = await create_profile(
            user_id,
            str(payload.get("name") or "New Profile"),
            blank_config_from(active.get("config") or {}),
            activate=False,
        )
    except ValueError as exc:
        return _json_error(str(exc), status.HTTP_400_BAD_REQUEST)
    return JSONResponse({"profile": _serialize_profile(created)}, headers={"Cache-Control": "no-store"})


@router.post("/api/profile-setup/save")
async def profile_setup_save(request: Request) -> JSONResponse:
    try:
        payload = await request.json()
    except Exception:
        return _json_error("invalid request", status.HTTP_400_BAD_REQUEST)
    user_id = _auth(payload)
    if user_id is None:
        return _json_error("unauthorized", status.HTTP_401_UNAUTHORIZED)
    profile_id = str(payload.get("profile_id") or "")
    profile = await get_profile(user_id, profile_id)
    if profile is None:
        return _json_error("profile not found", status.HTTP_404_NOT_FOUND)
    try:
        config = _clean_config(payload.get("config"), dict(profile.get("config") or {}))
        saved = await save_profile(user_id, profile_id, name=str(payload.get("name") or profile.get("name") or "Profile"), config=config)
    except (TypeError, ValueError) as exc:
        return _json_error(str(exc), status.HTTP_400_BAD_REQUEST)
    return JSONResponse({"profile": _serialize_profile(saved), "saved": True}, headers={"Cache-Control": "no-store"})


PROFILE_SETUP_HTML = r'''<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover,user-scalable=no">
<title>Plane Alerts · Visual Setup</title><script src="https://telegram.org/js/telegram-web-app.js"></script>
<style>
:root{color-scheme:light dark;--bg:var(--tg-theme-bg-color,#fff);--surface:var(--tg-theme-secondary-bg-color,#f4f4f5);--text:var(--tg-theme-text-color,#111);--hint:var(--tg-theme-hint-color,#737373);--link:var(--tg-theme-link-color,#2678b7);--button:var(--tg-theme-button-color,#2678b7);--buttonText:var(--tg-theme-button-text-color,#fff);--line:color-mix(in srgb,var(--hint) 25%,transparent);--danger:var(--tg-theme-destructive-text-color,#c33)}
*{box-sizing:border-box}html,body{margin:0;min-height:100%;background:var(--bg);color:var(--text)}body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;-webkit-font-smoothing:antialiased}.app{max-width:760px;margin:0 auto;padding:calc(12px + env(safe-area-inset-top)) 16px calc(92px + env(safe-area-inset-bottom))}.head{padding:10px 2px 17px;border-bottom:1px solid var(--line)}.kicker{font-size:11px;letter-spacing:.11em;text-transform:uppercase;color:var(--hint);font-weight:700}.head h1{font-size:25px;letter-spacing:-.025em;margin:6px 0 4px}.sub{font-size:13px;color:var(--hint);line-height:1.45}.nav{display:flex;overflow:auto;border-bottom:1px solid var(--line);position:sticky;top:0;background:var(--bg);z-index:3}.nav button{border:0;background:none;color:var(--hint);padding:14px 12px 12px;font:inherit;font-size:12px;white-space:nowrap;border-bottom:2px solid transparent}.nav button.on{color:var(--text);border-bottom-color:var(--button);font-weight:650}.section{display:none;padding:20px 2px}.section.on{display:block}.section h2{font-size:17px;margin:0 0 15px}.row{min-height:49px;border-bottom:1px solid var(--line);display:grid;grid-template-columns:minmax(0,1fr) auto;gap:14px;align-items:center}.row.stack{grid-template-columns:1fr;padding:9px 0}.label{font-size:14px}.hint{font-size:11px;color:var(--hint);margin-top:3px;line-height:1.35}input,select{font:inherit;color:var(--text);background:var(--surface);border:1px solid var(--line);border-radius:7px;padding:9px 10px;min-width:96px;outline:none}input:focus,select:focus{border-color:var(--button)}input[type=number]{width:118px}.wide{width:100%}.switch{width:42px;height:24px;appearance:none;background:var(--surface);border:1px solid var(--line);border-radius:14px;position:relative;padding:0}.switch:after{content:"";position:absolute;left:3px;top:3px;width:16px;height:16px;border-radius:50%;background:var(--hint);transition:.12s}.switch:checked{background:var(--button)}.switch:checked:after{left:21px;background:var(--buttonText)}.cats{border-top:1px solid var(--line);margin-top:13px}.cat{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:12px;padding:12px 0;border-bottom:1px solid var(--line)}.cat b{font-size:13px}.count{font-size:11px;color:var(--hint)}.search{display:flex;gap:8px;margin-bottom:12px}.search input{flex:1}.smallbtn{border:1px solid var(--line);background:var(--surface);color:var(--text);border-radius:7px;padding:8px 11px;font:inherit;font-size:12px}.airlist{border-top:1px solid var(--line)}.air{display:grid;grid-template-columns:auto minmax(0,1fr) auto;gap:10px;align-items:center;padding:10px 0;border-bottom:1px solid var(--line)}.code{font:600 12px ui-monospace,SFMono-Regular,Menlo,monospace}.model{font-size:13px}.family{font-size:11px;color:var(--hint);margin-top:2px}.rulebox{margin:14px 0;border-top:1px solid var(--line)}.rulehead{padding:12px 0;border-bottom:1px solid var(--line);font-size:13px;font-weight:650}.inherit{font-size:11px;color:var(--hint);font-weight:400;margin-left:6px}.review{font-size:13px;line-height:1.65}.review strong{font-weight:650}.footer{position:fixed;left:0;right:0;bottom:0;background:var(--bg);border-top:1px solid var(--line);padding:10px 16px calc(10px + env(safe-area-inset-bottom));display:flex;gap:9px;z-index:5}.footer>div{max-width:728px;width:100%;margin:auto;display:flex;gap:9px}.primary,.secondary{min-height:43px;border-radius:8px;padding:0 15px;font:650 14px inherit}.primary{flex:1;background:var(--button);color:var(--buttonText);border:1px solid var(--button)}.secondary{background:var(--surface);color:var(--text);border:1px solid var(--line)}.status{font-size:12px;color:var(--hint);padding-top:9px}.error{color:var(--danger)}
</style></head><body><main class="app"><header class="head"><div class="kicker">Plane Alerts · Visual Setup</div><h1 id="title">Profile</h1><div class="sub">The same settings used by Guided Setup. Changes take effect only when saved.</div></header><nav class="nav" id="nav"></nav><div id="sections"></div><div class="status" id="status"></div></main><footer class="footer"><div><button class="secondary" id="back">Back</button><button class="primary" id="next">Next</button></div></footer>
<script>
const tg=window.Telegram?.WebApp; if(tg){tg.ready();tg.expand();tg.BackButton.show();tg.BackButton.onClick(()=>history.length>1?history.back():tg.close());if(tg.themeParams?.bg_color)tg.setBackgroundColor(tg.themeParams.bg_color)}
const steps=['Profile','Location','Alert Radius','Aircraft','Advanced Rules','Camera','Review'];let step=0,state=null,catalogue={page:0,query:'',category:''};
const $=s=>document.querySelector(s), esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function api(path,body={}){const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({init_data:tg?.initData||'',...body})});const j=await r.json();if(!r.ok)throw new Error(j.detail||'Request failed');return j}
function prefs(){state.profile.config.preferences??={};return state.profile.config.preferences}function filter(){let p=prefs();p.aircraft_filter??={mode:'selected',selected_categories:[],selected_types:[],excluded_types:[]};return p.aircraft_filter}function rules(){let p=prefs();p.filter_rules??={profile:{},categories:{},aircraft:{}};return p.filter_rules}
function render(){document.querySelector('#nav').innerHTML=steps.map((x,i)=>`<button class="${i===step?'on':''}" onclick="goto(${i})">${x}</button>`).join('');document.querySelector('#sections').innerHTML=steps.map((x,i)=>`<section class="section ${i===step?'on':''}" id="s${i}"></section>`).join('');document.querySelector('#title').textContent=state.profile.name;renderStep();$('#back').textContent=step===0?'Close':'Back';$('#next').textContent=step===steps.length-1?'Save':'Next'}
function renderStep(){const cfg=state.profile.config,loc=cfg.location||{},f=filter(),r=rules(),cam=cfg.camera||{},cl=state.profile.camera_labels||{};let h='';
if(step===0)h=`<h2>Profile</h2><div class="row"><div><div class="label">Name</div><div class="hint">Visible in /profiles.</div></div><input id="name" maxlength="40" value="${esc(state.profile.name)}"></div><div class="row"><div><div class="label">Profile</div><div class="hint">Switch profiles from /profiles. Visual Setup edits only this profile.</div></div><select id="profileSel">${state.profiles.map(p=>`<option value="${p.profile_id}" ${p.profile_id===state.profile.profile_id?'selected':''}>${esc(p.name)}</option>`).join('')}</select></div>`;
if(step===1)h=`<h2>Location</h2><div class="row"><span class="label">Latitude</span><input id="lat" inputmode="decimal" value="${loc.latitude??''}"></div><div class="row"><span class="label">Longitude</span><input id="lon" inputmode="decimal" value="${loc.longitude??''}"></div><div class="hint" style="padding-top:10px">Use the same observing location as Guided Setup. Exact coordinates remain private to your profile.</div>`;
if(step===2)h=`<h2>Alert Radius</h2><div class="row"><div><div class="label">Radius</div><div class="hint">1–150 km. Aircraft-specific rules can override this.</div></div><input id="radius" type="number" min="1" max="150" step="1" value="${loc.radius_km??15}"></div>`;
if(step===3)h=`<h2>Aircraft</h2><div class="row"><div><div class="label">All Aircraft</div><div class="hint">Dynamic catalogue: new aircraft types remain included automatically.</div></div><input class="switch" id="all" type="checkbox" ${f.mode==='all'?'checked':''}></div><div class="search"><input id="q" placeholder="Search type, model, family or manufacturer" value="${esc(catalogue.query)}"><button class="smallbtn" onclick="searchNow()">Search</button></div><div class="cats">${state.categories.map(c=>`<div class="cat"><div><b>${esc(c.label)}</b><div class="count">${c.count} catalogue types</div></div><input class="switch catToggle" data-cat="${c.key}" type="checkbox" ${(f.selected_categories||[]).includes(c.key)?'checked':''}></div>`).join('')}</div><div id="airlist" class="airlist"></div>`;
if(step===4){const pr=r.profile||{};h=`<h2>Advanced Rules</h2><div class="hint">Inheritance: Profile defaults → Category override → Aircraft-specific override. Missing fields inherit automatically.</div>${ruleEditor('Profile defaults','profile','',pr,'Base values used unless a more specific rule overrides them.')}<div class="row stack"><div class="label">Category override</div><select class="wide" id="catRule"><option value="">Choose category…</option>${state.categories.map(c=>`<option value="${c.key}">${esc(c.label)}</option>`).join('')}</select></div><div class="row stack"><div class="label">Aircraft-specific override</div><input class="wide" id="airRule" placeholder="ICAO type, e.g. AN12 or A359"></div><div id="specificRule"></div>`}
if(step===5)h=`<h2>Camera</h2><div class="row stack"><div><div class="label">Camera body</div><div class="hint">Known camera geometry is resolved locally.</div></div><input class="wide" id="cameraText" placeholder="Canon EOS R7" value="${esc(cl.camera_text||cam.camera_text||'')}"></div><div class="row stack"><div><div class="label">Lens</div><div class="hint">Example: Canon RF 200-800mm.</div></div><input class="wide" id="lensText" placeholder="Canon RF 200-800mm" value="${esc(cl.lens_text||cam.lens_text||'')}"></div>`;
if(step===6){const sel=f.mode==='all'?'All Aircraft':`${(f.selected_categories||[]).length} categories · ${(f.selected_types||[]).length} individual types`;h=`<h2>Review</h2><div class="review"><strong>Profile:</strong> ${esc(state.profile.name)}<br><strong>Location:</strong> ${loc.latitude??'not set'}, ${loc.longitude??'not set'}<br><strong>Radius:</strong> ${loc.radius_km??15} km<br><strong>Aircraft:</strong> ${esc(sel)}<br><strong>Category overrides:</strong> ${Object.keys(r.categories||{}).length}<br><strong>Aircraft overrides:</strong> ${Object.keys(r.aircraft||{}).length}<br><strong>Camera:</strong> ${esc(cl.camera_text||'not set')}<br><strong>Lens:</strong> ${esc(cl.lens_text||'not set')}</div><div class="hint" style="padding-top:14px">Saving updates this profile. If it is active, the live worker sees the materialized settings immediately without adding profile database work to the five-second loop.</div>`}
$('#s'+step).innerHTML=h;bind();if(step===3)loadCatalogue()}
function ruleEditor(title,scope,target,obj,hint){return `<div class="rulebox" data-scope="${scope}" data-target="${target}"><div class="rulehead">${title}<span class="inherit">${hint}</span></div><div class="row"><span class="label">Enabled</span><select data-k="enabled"><option value="inherit" ${obj.enabled===undefined?'selected':''}>Inherit</option><option value="true" ${obj.enabled===true?'selected':''}>On</option><option value="false" ${obj.enabled===false?'selected':''}>Off</option></select></div><div class="row"><span class="label">Min altitude (ft)</span><input data-k="min_altitude_ft" inputmode="numeric" value="${obj.min_altitude_ft??''}" placeholder="Inherit"></div><div class="row"><span class="label">Max altitude (ft)</span><input data-k="max_altitude_ft" inputmode="numeric" value="${obj.max_altitude_ft??''}" placeholder="Inherit"></div><div class="row"><span class="label">Radius override (km)</span><input data-k="radius_km" inputmode="decimal" value="${obj.radius_km??''}" placeholder="Inherit"></div><div class="row stack"><span class="label">Operators</span><input data-k="airlines" class="wide" value="${esc((obj.airlines||[]).join(', '))}" placeholder="THY, FDX"><select data-k="airline_mode" class="wide"><option value="inherit">Inherit</option><option value="all" ${obj.airline_mode==='all'?'selected':''}>All operators</option><option value="whitelist" ${obj.airline_mode==='whitelist'?'selected':''}>Allow list</option><option value="blacklist" ${obj.airline_mode==='blacklist'?'selected':''}>Block list</option></select></div>${scope==='profile'?'':`<button class="smallbtn" onclick="resetRule('${scope}','${target}')">Reset to inherited settings</button>`}</div>`}
function bind(){if(step===0){$('#name').oninput=e=>state.profile.name=e.target.value;$('#profileSel').onchange=e=>load(e.target.value)}if(step===1){$('#lat').oninput=e=>(state.profile.config.location??={}).latitude=e.target.value;$('#lon').oninput=e=>(state.profile.config.location??={}).longitude=e.target.value}if(step===2)$('#radius').oninput=e=>(state.profile.config.location??={}).radius_km=Number(e.target.value);if(step===3){$('#all').onchange=e=>filter().mode=e.target.checked?'all':'selected';document.querySelectorAll('.catToggle').forEach(x=>x.onchange=e=>toggleList(filter().selected_categories??=[],e.target.dataset.cat,e.target.checked))}if(step===4){bindRule($('#s4 .rulebox'),rules().profile??={});$('#catRule').onchange=e=>{if(!e.target.value)return;rules().categories??={};rules().categories[e.target.value]??={};$('#specificRule').innerHTML=ruleEditor(state.categories.find(x=>x.key===e.target.value)?.label||e.target.value,'category',e.target.value,rules().categories[e.target.value],'Overrides profile defaults.');bindRule($('#specificRule .rulebox'),rules().categories[e.target.value])};$('#airRule').onchange=e=>{const code=e.target.value.trim().toUpperCase();if(!code)return;rules().aircraft??={};rules().aircraft[code]??={};$('#specificRule').innerHTML=ruleEditor(code,'aircraft',code,rules().aircraft[code],'Most specific setting; overrides category and profile.');bindRule($('#specificRule .rulebox'),rules().aircraft[code])}}if(step===5){$('#cameraText').oninput=e=>(state.profile.config.camera??={}).camera_text=e.target.value;$('#lensText').oninput=e=>(state.profile.config.camera??={}).lens_text=e.target.value}}
function bindRule(el,obj){if(!el)return;el.querySelectorAll('[data-k]').forEach(x=>x.onchange=e=>{const k=e.target.dataset.k,v=e.target.value;if(v===''||v==='inherit'){delete obj[k];return}if(k==='enabled')obj[k]=v==='true';else if(['min_altitude_ft','max_altitude_ft','radius_km'].includes(k))obj[k]=Number(v);else if(k==='airlines')obj[k]=v.split(',').map(x=>x.trim().toUpperCase()).filter(Boolean);else obj[k]=v})}
function resetRule(scope,target){if(scope==='category')delete rules().categories[target];else if(scope==='aircraft')delete rules().aircraft[target];$('#specificRule').innerHTML='<div class="hint">Override removed. This item now uses inherited settings.</div>'}
function toggleList(list,val,on){const i=list.indexOf(val);if(on&&i<0)list.push(val);if(!on&&i>=0)list.splice(i,1)}
async function loadCatalogue(){const j=await api('/api/profile-setup/catalogue',{query:catalogue.query,category:catalogue.category,page:catalogue.page});const selected=filter().selected_types??=[];$('#airlist').innerHTML=j.items.map(a=>`<label class="air"><input type="checkbox" data-code="${a.code}" ${selected.includes(a.code)?'checked':''}><div><div class="model">${esc(a.manufacturer)} ${esc(a.model)}</div><div class="family">${esc(a.family)}</div></div><span class="code">${a.code}</span></label>`).join('')+(j.has_more?`<button class="smallbtn" onclick="catalogue.page++;loadCatalogue()">Next ${Math.min(40,j.total-(j.page+1)*40)}</button>`:'');document.querySelectorAll('#airlist input[data-code]').forEach(x=>x.onchange=e=>toggleList(selected,e.target.dataset.code,e.target.checked))}
function searchNow(){catalogue.query=$('#q').value.trim();catalogue.page=0;loadCatalogue()}function goto(i){step=Math.max(0,Math.min(steps.length-1,i));render()}
async function save(){setStatus('Saving…');try{const j=await api('/api/profile-setup/save',{profile_id:state.profile.profile_id,name:state.profile.name,config:state.profile.config});state.profile=j.profile;setStatus('Saved. Guided Setup now shows the same values.');if(tg?.HapticFeedback)tg.HapticFeedback.notificationOccurred('success');render()}catch(e){setStatus(e.message,true)}}function setStatus(t,err=false){$('#status').textContent=t;$('#status').className='status'+(err?' error':'')}
$('#back').onclick=()=>{if(step===0){tg?.close()}else{step--;render()}};$('#next').onclick=()=>{if(step===steps.length-1)save();else{step++;render()}};
async function load(id=''){setStatus('Loading…');try{state=await api('/api/profile-setup/bootstrap',{profile_id:id});setStatus('');render()}catch(e){setStatus(e.message,true)}}load();
</script></body></html>'''
