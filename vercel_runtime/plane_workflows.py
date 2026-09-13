"""Durable Vercel workflows for the Plane? Telegram aircraft bot.

The durable workflow layer is intentionally tiny.  The actual application source is
loaded from the pinned public GitHub commit for each deployment generation.  Runtime
credentials are passed as Vercel Workflow inputs, whose payloads are encrypted by the
platform, so no application secret needs to be committed to the repository or copied
into Vercel project environment variables.
"""
from __future__ import annotations

import hashlib
import io
import logging
import os
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from vercel.workflow import BaseHook, Workflows, sleep, start

wf = Workflows(namespace="plane-telegram-v32")
logger = logging.getLogger(__name__)

REPOSITORY = "xtenrore/Gemini-Telegram-Bot-Exprience"
MONITOR_INTERVAL = "3m"
MONITOR_CYCLES_PER_RUN = 480  # one day, then chain into a fresh run
SERVERLESS_EARLY_WARNING_BUFFER_KM = 70.0

_loaded_generation: str | None = None
_loaded_source_path: str | None = None


@dataclass
class TelegramUpdate(BaseHook):
    """A Telegram update delivered to the long-lived workflow hook."""

    update: dict[str, Any]


def webhook_path_secret(telegram_token: str, generation: str) -> str:
    material = f"plane-v3.2|{generation}|{telegram_token}".encode()
    return hashlib.sha256(material).hexdigest()[:48]


def telegram_secret_header(path_secret: str) -> str:
    return hashlib.sha256(f"telegram-header|{path_secret}".encode()).hexdigest()[:48]


def _download_source(generation: str) -> str:
    """Load one immutable repository commit into /tmp and return its root path."""
    global _loaded_generation, _loaded_source_path

    if _loaded_generation == generation and _loaded_source_path and Path(_loaded_source_path).exists():
        return _loaded_source_path

    cache_root = Path("/tmp/plane-source") / generation
    marker = cache_root / ".ready"
    if marker.exists():
        source_root = str(cache_root)
    else:
        parent = cache_root.parent
        parent.mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(prefix=f"plane-{generation[:10]}-", dir=str(parent)))
        try:
            url = f"https://codeload.github.com/{REPOSITORY}/zip/{generation}"
            request = urllib.request.Request(url, headers={"User-Agent": "plane-bot-v3.2"})
            with urllib.request.urlopen(request, timeout=25) as response:
                payload = response.read()
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                for info in archive.infolist():
                    parts = Path(info.filename).parts
                    if len(parts) <= 1:
                        continue
                    relative = Path(*parts[1:])
                    if relative.is_absolute() or ".." in relative.parts:
                        continue
                    target = tmp / relative
                    if info.is_dir():
                        target.mkdir(parents=True, exist_ok=True)
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(info) as source, target.open("wb") as dest:
                        shutil.copyfileobj(source, dest)

            # Vercel's no-card Hobby runtime checks less often than the original
            # container worker. Keep the native trajectory engine, but widen only
            # this runtime's early-warning envelope so a fast jet cannot cross the
            # watched area between durable monitor cycles.
            monitor_file = tmp / "app" / "worker" / "monitor.py"
            text = monitor_file.read_text(encoding="utf-8")
            text = text.replace(
                'radius = loc.get("radius_km", settings.default_radius_km) + 15.0',
                f'radius = loc.get("radius_km", settings.default_radius_km) + {SERVERLESS_EARLY_WARNING_BUFFER_KM}',
            )
            text = text.replace(
                "outer_buffer_km = 15.0",
                f"outer_buffer_km = {SERVERLESS_EARLY_WARNING_BUFFER_KM}",
            )
            monitor_file.write_text(text, encoding="utf-8")
            (tmp / ".ready").write_text(generation, encoding="utf-8")

            try:
                os.rename(tmp, cache_root)
            except FileExistsError:
                shutil.rmtree(tmp, ignore_errors=True)
            source_root = str(cache_root)
        except Exception:
            shutil.rmtree(tmp, ignore_errors=True)
            raise

    if _loaded_generation != generation:
        for name in list(sys.modules):
            if name == "app" or name.startswith("app."):
                sys.modules.pop(name, None)
    if source_root not in sys.path:
        sys.path.insert(0, source_root)
    _loaded_generation = generation
    _loaded_source_path = source_root
    return source_root


def _apply_runtime_config(config: dict[str, Any]) -> None:
    from app.config import settings

    settings.telegram_bot_token = str(config["telegram_bot_token"])
    settings.mongo_uri = str(config["mongo_uri"])
    settings.database_name = str(config.get("database_name") or "aircraft_bot")
    settings.gemini_api_key = str(config["gemini_api_key"])
    settings.groq_api_key = str(config.get("groq_api_key") or "")
    settings.opensky_credentials_json = str(config.get("opensky_credentials_json") or "")
    settings.admin_password = str(config.get("admin_password") or "")
    admin_id = config.get("admin_telegram_id")
    settings.admin_telegram_id = int(admin_id) if admin_id not in (None, "") else None
    settings.webhook_url = ""
    settings.webhook_secret = ""


async def _runtime_is_current(generation: str) -> bool:
    from app.database import system_status_col

    doc = await system_status_col().find_one({"_id": "vercel_runtime"}, {"generation": 1, "state": 1})
    return bool(doc and doc.get("generation") == generation and doc.get("state") == "running")


@wf.step
async def configure_telegram(
    config: dict[str, Any],
    generation: str,
    base_url: str,
) -> dict[str, Any]:
    """Install Telegram webhook + command menu and publish a safe runtime heartbeat."""
    token = str(config["telegram_bot_token"])
    path_secret = webhook_path_secret(token, generation)
    secret_header = telegram_secret_header(path_secret)
    webhook_url = f"{base_url.rstrip('/')}/telegram/{path_secret}"
    api = f"https://api.telegram.org/bot{token}"

    commands = [
        {"command": "start", "description": "Set up aircraft alerts"},
        {"command": "status", "description": "Show monitoring configuration"},
        {"command": "location", "description": "Update monitoring / shooting location"},
        {"command": "preferences", "description": "Choose aircraft categories and types"},
        {"command": "camera", "description": "Tell Gemini your camera body"},
        {"command": "lens", "description": "Tell Gemini your aircraft lens"},
        {"command": "conditions", "description": "Weather, atmosphere and sun geometry"},
        {"command": "photo", "description": "Live Gemini best-shot settings"},
        {"command": "help", "description": "Show commands"},
    ]

    async with httpx.AsyncClient(timeout=20.0) as client:
        me = await client.get(f"{api}/getMe")
        me.raise_for_status()
        identity = me.json().get("result") or {}
        webhook = await client.post(
            f"{api}/setWebhook",
            json={
                "url": webhook_url,
                "secret_token": secret_header,
                "drop_pending_updates": False,
                "allowed_updates": ["message", "callback_query"],
            },
        )
        webhook.raise_for_status()
        if not webhook.json().get("ok"):
            raise RuntimeError("Telegram rejected webhook configuration")
        menu = await client.post(f"{api}/setMyCommands", json={"commands": commands})
        menu.raise_for_status()

    _download_source(generation)
    _apply_runtime_config(config)
    from app.database import close_db, connect_db, system_status_col

    await connect_db(max_retries=2, retry_delay=0.5, timeout_ms=8000)
    try:
        await system_status_col().update_one(
            {"_id": "telegram_runtime"},
            {"$set": {
                "generation": generation,
                "bot_username": identity.get("username", ""),
                "webhook_host": base_url,
                "ready": True,
            }},
            upsert=True,
        )
    finally:
        await close_db()

    return {"username": identity.get("username", ""), "webhook": True}


@wf.step
async def process_telegram_update(
    config: dict[str, Any],
    generation: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Run one existing v3.2 Telegram update through the production handlers."""
    _download_source(generation)
    _apply_runtime_config(config)

    from telegram import Update
    from telegram.ext import Application
    from app.bot.handlers import register_handlers
    from app.database import close_db, connect_db
    from app.photography.telegram import register_photography_handlers

    await connect_db(max_retries=2, retry_delay=0.5, timeout_ms=8000)
    application = Application.builder().token(str(config["telegram_bot_token"])).build()
    register_photography_handlers(application)
    register_handlers(application)

    try:
        await application.initialize()
        await application.start()
        update = Update.de_json(payload, application.bot)
        if update is not None:
            await application.process_update(update)
        return {"processed": True, "update_id": payload.get("update_id")}
    except Exception as exc:
        # Telegram may retry webhook delivery; do not ask Workflow to replay a
        # partially side-effecting handler automatically as that can duplicate replies.
        logger.exception("Telegram update processing failed: %s", type(exc).__name__)
        return {"processed": False, "update_id": payload.get("update_id")}
    finally:
        try:
            if application.running:
                await application.stop()
            await application.shutdown()
        finally:
            await close_db()


@wf.step
async def monitor_cycle_step(config: dict[str, Any], generation: str) -> bool:
    """Run exactly one ADS-B monitor cycle. Return False when superseded."""
    _download_source(generation)
    _apply_runtime_config(config)

    from app.database import close_db, connect_db
    from app.worker.monitor import init_services, run_monitor_cycle

    await connect_db(max_retries=2, retry_delay=0.5, timeout_ms=8000)
    try:
        if not await _runtime_is_current(generation):
            return False
        await init_services()
        await run_monitor_cycle()
        from app.database import system_status_col
        from datetime import datetime, timezone
        await system_status_col().update_one(
            {"_id": "vercel_monitor"},
            {"$set": {
                "generation": generation,
                "ready": True,
                "updated_at": datetime.now(timezone.utc),
            }},
            upsert=True,
        )
        return True
    finally:
        await close_db()


@wf.workflow
async def telegram_workflow(
    config: dict[str, Any],
    generation: str,
    base_url: str,
) -> None:
    await configure_telegram(config=config, generation=generation, base_url=base_url)
    path_secret = webhook_path_secret(str(config["telegram_bot_token"]), generation)
    hook_token = f"telegram:{path_secret}"

    async for event in TelegramUpdate.wait(token=hook_token):
        await process_telegram_update(config=config, generation=generation, payload=event.update)


@wf.workflow
async def monitor_workflow(config: dict[str, Any], generation: str) -> None:
    for _ in range(MONITOR_CYCLES_PER_RUN):
        active = await monitor_cycle_step(config=config, generation=generation)
        if not active:
            return
        await sleep(MONITOR_INTERVAL)

    # Keep individual event logs bounded while continuing indefinitely.
    await start(monitor_workflow, config, generation)
