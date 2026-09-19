"""Runtime hardening for the Plane Alerts v4.4 profile Mini App.

This keeps the original Mini App implementation small while fixing production
URL/bootstrap behavior and first-run onboarding semantics in one place.
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from app.aircraft.filtering import normalized_filter_config
from app.alert_profiles import get_profile
from app.database import users_col


def _patched_html(source: str) -> str:
    html = source
    html = html.replace("<title>Plane Alerts · Visual Setup</title>", "<title>Profile Setup</title>")
    html = html.replace('<div class="kicker">Plane Alerts · Visual Setup</div>', "")
    html = html.replace(
        "const steps=['Profile','Location','Alert Radius','Aircraft','Advanced Rules','Camera','Review'];let step=0,state=null,catalogue={page:0,query:'',category:''};",
        "const onboarding=new URLSearchParams(window.location.search).get('onboarding')==='1';const steps=['Profile','Location','Alert Radius','Aircraft','Advanced Rules','Camera','Review'];let step=0,state=null,catalogue={page:0,query:'',category:''};",
    )
    html = html.replace(
        "async function api(path,body={}){const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({init_data:tg?.initData||'',...body})});",
        "async function api(path,body={}){const target=window.location.origin+path;const r=await fetch(target,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({init_data:tg?.initData||'',...body})});",
    )
    html = html.replace(
        "async function save(){setStatus('Saving…');try{const j=await api('/api/profile-setup/save',{profile_id:state.profile.profile_id,name:state.profile.name,config:state.profile.config});state.profile=j.profile;setStatus('Saved. Guided Setup now shows the same values.');",
        "async function save(){setStatus('Saving…');try{const j=await api('/api/profile-setup/save',{profile_id:state.profile.profile_id,name:state.profile.name,config:state.profile.config,complete_setup:onboarding});state.profile=j.profile;if(onboarding&&!j.setup_complete){setStatus(j.setup_error||'Complete the required setup fields before finishing.',true);return}setStatus(onboarding?'Saved. Setup complete.':'Saved. Guided Setup now shows the same values.');",
    )
    return html


def install_profile_miniapp_html_patch() -> None:
    """Patch both route modules because profile_miniapp_entry imports the HTML by value."""
    import app.profile_miniapp as mini
    import app.profile_miniapp_entry as entry

    patched = _patched_html(mini.PROFILE_SETUP_HTML)
    mini.PROFILE_SETUP_HTML = patched
    entry.PROFILE_SETUP_HTML = patched


def _onboarding_ready(profile: dict[str, Any] | None) -> tuple[bool, str]:
    if not profile:
        return False, "Profile unavailable."
    config = dict(profile.get("config") or {})
    location = dict(config.get("location") or {})
    if location.get("latitude") is None or location.get("longitude") is None:
        return False, "Set a location before finishing setup."
    prefs = dict(config.get("preferences") or {})
    selection = normalized_filter_config(prefs)
    if (
        selection.get("mode") != "all"
        and not selection.get("selected_categories")
        and not selection.get("selected_types")
    ):
        return False, "Select at least one aircraft/category, or enable All Aircraft."
    return True, ""


def install_profile_save_onboarding_upgrade(app) -> None:
    """Allow Visual Setup to finish the same first-run flow as Guided Setup."""
    import app.profile_miniapp as mini

    for route in app.routes:
        if getattr(route, "path", None) != "/api/profile-setup/save":
            continue
        old = getattr(route, "endpoint", None)
        if old is None:
            continue

        async def endpoint(request: Request, __old=old):
            try:
                payload = await request.json()
            except Exception:
                payload = {}
            response = await __old(request)
            if not payload.get("complete_setup") or getattr(response, "status_code", 500) >= 400:
                return response

            user_id = mini._auth(payload)
            profile_id = str(payload.get("profile_id") or "")
            profile = await get_profile(user_id, profile_id) if user_id is not None else None
            ready, error = _onboarding_ready(profile)

            try:
                body = json.loads(bytes(response.body).decode("utf-8"))
            except Exception:
                body = {"saved": True}

            if ready and user_id is not None:
                await users_col().update_one(
                    {"user_id": int(user_id)},
                    {"$set": {"setup_complete": True}},
                    upsert=True,
                )
                body["setup_complete"] = True
            else:
                body["setup_complete"] = False
                body["setup_error"] = error or "Setup is incomplete."

            return JSONResponse(body, status_code=response.status_code, headers={"Cache-Control": "no-store"})

        route.endpoint = endpoint
        dependant = getattr(route, "dependant", None)
        if dependant is not None:
            dependant.call = endpoint
        break
