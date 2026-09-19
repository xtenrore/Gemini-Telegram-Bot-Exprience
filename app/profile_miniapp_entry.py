"""Context-aware entry URLs for the v4.4 Visual Setup Mini App."""
from __future__ import annotations

import re

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app.profile_miniapp import PROFILE_SETUP_HTML

router = APIRouter()
_PROFILE_RE = re.compile(r"^[0-9a-f]{10}$")


def _html(start: str) -> HTMLResponse:
    # The source HTML ends with one standalone load() call. Replace only that
    # final bootstrap call; no untrusted value is interpolated without strict
    # profile-id validation.
    content = PROFILE_SETUP_HTML.rsplit("load();", 1)[0] + start + PROFILE_SETUP_HTML.rsplit("load();", 1)[1]
    return HTMLResponse(content, headers={"Cache-Control": "no-store"})


@router.get("/profile-setup-ui/profile/{profile_id}", response_class=HTMLResponse)
async def profile_setup_selected(profile_id: str) -> HTMLResponse:
    value = profile_id if _PROFILE_RE.fullmatch(profile_id) else ""
    return _html(f"load('{value}');")


@router.get("/profile-setup-ui/new", response_class=HTMLResponse)
async def profile_setup_new() -> HTMLResponse:
    return _html("api('/api/profile-setup/create',{name:'New Profile'}).then(x=>load(x.profile.profile_id)).catch(e=>setStatus(e.message,true));")
