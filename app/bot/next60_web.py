from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qsl

MAX_INIT_DATA_AGE_S = 3600


def validate_telegram_init_data(init_data: str, bot_token: str, *, now: float | None = None) -> int | None:
    if not init_data or not bot_token:
        return None
    values = dict(parse_qsl(init_data, keep_blank_values=True))
    supplied_hash = values.pop("hash", "")
    if not supplied_hash:
        return None
    data_check_string = "\n".join(f"{key}={values[key]}" for key in sorted(values))
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    calculated_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calculated_hash, supplied_hash):
        return None
    try:
        auth_date = int(values.get("auth_date", "0"))
    except (TypeError, ValueError):
        return None
    current = time.time() if now is None else now
    if auth_date <= 0 or auth_date > current + 60 or current - auth_date > MAX_INIT_DATA_AGE_S:
        return None
    try:
        user = json.loads(values.get("user", "{}"))
        return int(user["id"])
    except (TypeError, ValueError, KeyError, json.JSONDecodeError):
        return None


def _aware(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def serialize_next60(now: datetime, docs: list[dict[str, Any]]) -> dict[str, Any]:
    now = now.astimezone(timezone.utc)
    rows: list[dict[str, Any]] = []
    for doc in docs:
        cpa = _aware(doc.get("predicted_cpa_at"))
        if cpa is None:
            continue
        minutes = max(0, int(round((cpa - now).total_seconds() / 60.0)))
        if minutes > 60:
            continue
        if minutes <= 15:
            bucket = "0–15"
        elif minutes <= 30:
            bucket = "15–30"
        else:
            bucket = "30–60"
        try:
            closest = round(float(doc.get("predicted_closest_km")), 1)
        except (TypeError, ValueError):
            closest = None
        rows.append({
            "callsign": str(doc.get("callsign") or doc.get("aircraft_icao24") or "Unknown").upper(),
            "aircraft_type": str(doc.get("aircraft_type") or "Aircraft"),
            "minutes": minutes,
            "bucket": bucket,
            "closest_km": closest,
            "confidence": str(doc.get("confidence") or "Low").title(),
            "source": "live" if doc.get("source") == "live" else "shadow",
            "stage": str(doc.get("stage") or ""),
            "history_days": int(doc.get("historical_days") or 0),
        })
    return {"generated_at": now.isoformat(), "rows": rows, "count": len(rows)}


NEXT60_HTML = r'''<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>Plane? · Next 60</title><script src="https://telegram.org/js/telegram-web-app.js"></script>
<style>
:root{color-scheme:light dark;--bg:var(--tg-theme-bg-color,transparent);--text:var(--tg-theme-text-color,#111);--hint:var(--tg-theme-hint-color,#777);--link:var(--tg-theme-link-color,#168acd);--line:color-mix(in srgb,var(--hint) 28%,transparent);--live:#39a96b;--dev:#4d91c7;--shadow:#b28b43}
*{box-sizing:border-box}html,body{margin:0;min-height:100%;background:var(--bg);color:var(--text)}body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;-webkit-font-smoothing:antialiased}.shell{max-width:900px;margin:0 auto;padding:env(safe-area-inset-top) 20px calc(34px + env(safe-area-inset-bottom))}.top{position:sticky;top:0;z-index:5;background:var(--bg);padding:20px 0 17px;border-bottom:1px solid var(--line)}.eyebrow,.title{display:flex;align-items:center;justify-content:space-between;gap:16px}.brand{font-size:12px;letter-spacing:.14em;font-weight:760}.updated,.count{font-size:12px;color:var(--hint)}h1{font-size:clamp(30px,6vw,42px);letter-spacing:-.035em;margin:8px 0 0}.legend{display:flex;flex-wrap:wrap;gap:18px;margin-top:15px;font-size:12px;color:var(--hint)}.legend span{display:flex;align-items:center;gap:7px}.dot,.mark{width:7px;height:7px;border-radius:50%}.live{background:var(--live)}.dev{background:var(--dev)}.shadow{background:var(--shadow)}.section{padding-top:30px}.section-head{display:grid;grid-template-columns:72px 1fr auto;gap:18px;align-items:end;padding:0 4px 13px;border-bottom:1px solid var(--line)}.section-head b{font-size:12px;letter-spacing:.08em}.section-head span{font-size:12px;color:var(--hint)}.source{text-transform:uppercase;letter-spacing:.06em;font-size:10px!important}.row{display:grid;grid-template-columns:66px 84px minmax(0,1fr) 118px;gap:18px;align-items:center;min-height:124px;padding:13px 4px;border-bottom:1px solid var(--line)}.eta{align-self:stretch;display:flex;flex-direction:column;justify-content:center;border-right:1px solid var(--line);padding-right:16px}.eta strong{font-size:21px}.eta small{font-size:10px;color:var(--hint);margin-top:7px;text-transform:uppercase;letter-spacing:.08em}.plane{display:grid;place-items:center;min-height:78px}.plane svg{width:68px;height:68px;fill:var(--text);opacity:.88}.id{min-width:0;padding:4px 0}.flight{display:block;font-size:20px;font-weight:730}.model{display:block;margin-top:5px;font-size:13px;color:var(--hint);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.status{display:flex;align-items:center;gap:8px;margin-top:11px;font-size:11px;color:var(--hint)}.mark{width:5px;height:5px}.metrics{text-align:right;padding-left:6px}.dist{font-size:18px;font-weight:690}.label{margin-top:4px;font-size:10px;color:var(--hint);text-transform:uppercase;letter-spacing:.06em}.conf{margin-top:13px;font-size:10px;font-weight:760}.high{color:var(--live)}.medium{color:var(--dev)}.low,.uncertain{color:var(--shadow)}.empty{padding:34px 4px;color:var(--hint);border-bottom:1px solid var(--line)}.foot{padding:24px 4px 0;color:var(--hint);font-size:12px;line-height:1.55}.error{padding:40px 6px;color:var(--hint);line-height:1.5}
@media(max-width:620px){.shell{padding-left:18px;padding-right:18px}.section-head{grid-template-columns:58px 1fr;gap:12px}.source{display:none}.row{grid-template-columns:54px 58px minmax(0,1fr) 78px;gap:12px;min-height:118px;padding-top:16px;padding-bottom:16px}.eta{padding-right:11px}.eta strong{font-size:18px}.plane svg{width:54px;height:60px}.flight{font-size:18px}.model{font-size:12px}.status{margin-top:9px;font-size:10px}.dist{font-size:16px}.metrics{padding-left:2px}}
</style></head><body><main class="shell"><header class="top"><div class="eyebrow"><div class="brand">PLANE? · FORECAST</div><div class="updated" id="updated">loading</div></div><div class="title"><h1>Next 60 minutes</h1><div class="count" id="count"></div></div><div class="legend"><span><i class="dot live"></i>live trajectory</span><span><i class="dot dev"></i>developing</span><span><i class="dot shadow"></i>history shadow</span></div></header><div id="app"><div class="error">Loading forecast…</div></div><footer class="foot">Live CPA is authoritative when available. 30–60 minute history remains shadow-only.</footer></main>
<script>
const tg=window.Telegram&&window.Telegram.WebApp; if(tg){tg.ready();tg.expand();if(tg.themeParams&&tg.themeParams.bg_color)tg.setBackgroundColor(tg.themeParams.bg_color)}
const plane='M48 5c1-4 5-4 6 0l3 36 28 13c3 2 2 5-1 5l-27-6 3 23 10 8c2 2 1 4-1 4l-15-4-4 11c-1 3-4 3-5 0l-4-11-15 4c-2 0-3-2-1-4l10-8 3-23-27 6c-3 0-4-3-1-5l28-13 3-36z';
function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function section(label,subtitle,source,rows){let out=`<section class="section"><div class="section-head"><b>${label}</b><span>${subtitle}</span><span class="source">${source}</span></div>`;if(!rows.length)return out+'<div class="empty">No current candidates.</div></section>';for(const r of rows){const tone=r.source==='live'?(r.minutes<=15?'live':'dev'):'shadow';const conf=String(r.confidence||'Low').toLowerCase();const timing=r.minutes>30?'window':'to CPA';const status=r.source==='live'?(r.stage||'live geometry'):`history shadow · ${r.history_days||0} day${r.history_days===1?'':'s'}`;out+=`<article class="row"><div class="eta"><strong>${r.minutes}m</strong><small>${timing}</small></div><div class="plane"><svg viewBox="0 0 100 100" aria-hidden="true"><path d="${plane}"></path></svg></div><div class="id"><span class="flight">${esc(r.callsign)}</span><span class="model">${esc(r.aircraft_type||'Aircraft')}</span><div class="status"><i class="mark ${tone}"></i>${esc(status)}</div></div><div class="metrics"><div class="dist">${r.closest_km==null?'—':r.closest_km.toFixed(1)+' km'}</div><div class="label">${r.minutes>30?'projected':'closest'}</div><div class="conf ${conf}">${esc(r.confidence)}</div></div></article>`}return out+'</section>'}
async function load(){if(!tg||!tg.initData){document.getElementById('app').innerHTML='<div class="error">Open this view from the Plane? Telegram bot.</div>';return}try{const res=await fetch('/api/next60',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({init_data:tg.initData})});if(!res.ok)throw new Error('request failed');const data=await res.json();const rows=data.rows||[];document.getElementById('count').textContent=`${rows.length} aircraft`;document.getElementById('updated').textContent=new Date(data.generated_at).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'});const groups={a:rows.filter(r=>r.bucket==='0–15'),b:rows.filter(r=>r.bucket==='15–30'),c:rows.filter(r=>r.bucket==='30–60')};document.getElementById('app').innerHTML=section('0–15','Confirmed nearby trajectory','Live CPA',groups.a)+section('15–30','Trajectory developing','Mixed evidence',groups.b)+section('30–60','Longer-range forecast','Shadow only',groups.c)}catch(e){document.getElementById('app').innerHTML='<div class="error">Forecast could not be loaded. Close this view and try /next60 again.</div>'}}load();
</script></body></html>'''
