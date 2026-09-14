"""Side-effect helpers shared by Plane? v3.3 Vercel workflows.

This module deliberately contains no durable workflow definitions. Keeping application
loading, Mongo reuse, and Telegram Application reuse separate from workflow history lets
v3.3 use its own namespace without inheriting the v3.2 durable graph.
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import shutil
import sys
import tempfile
import time
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from vercel.workflow import BaseHook

for _noisy_logger in ("httpx", "httpcore", "telegram.request"):
    logging.getLogger(_noisy_logger).setLevel(logging.WARNING)

logger = logging.getLogger(__name__)
REPOSITORY = "xtenrore/Gemini-Telegram-Bot-Exprience"
SERVERLESS_EARLY_WARNING_BUFFER_KM = 70.0

_loaded_generation: str | None = None
_loaded_source_path: str | None = None
_telegram_application: Any | None = None
_telegram_generation: str | None = None
_telegram_runtime_lock: asyncio.Lock | None = None


@dataclass
class TelegramUpdate(BaseHook):
    update: dict[str, Any]


def _should_detach_telegram_update(payload: dict[str, Any]) -> bool:
    callback = payload.get("callback_query")
    if isinstance(callback, dict):
        data = callback.get("data")
        if isinstance(data, str) and data.startswith("photo:"):
            return True

    message = payload.get("message") or payload.get("edited_message")
    if isinstance(message, dict):
        text = message.get("text")
        if isinstance(text, str) and text.strip():
            command_token = text.strip().split(maxsplit=1)[0]
            command = command_token.split("@", 1)[0].lower()
            if command in {"/photo", "/conditions"}:
                return True
    return False


def _runtime_lock() -> asyncio.Lock:
    global _telegram_runtime_lock
    if _telegram_runtime_lock is None:
        _telegram_runtime_lock = asyncio.Lock()
    return _telegram_runtime_lock


def _download_source(generation: str) -> str:
    """Load the immutable application commit once per warm Vercel process."""
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
            request = urllib.request.Request(url, headers={"User-Agent": "plane-bot-v3.3"})
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

    doc = await system_status_col().find_one(
        {"_id": "vercel_runtime"},
        {"generation": 1, "state": 1},
    )
    return bool(doc and doc.get("generation") == generation and doc.get("state") == "running")


async def _shutdown_cached_telegram_runtime() -> None:
    global _telegram_application, _telegram_generation
    application = _telegram_application
    _telegram_application = None
    _telegram_generation = None
    if application is not None:
        try:
            if application.running:
                await application.stop()
            await application.shutdown()
        except Exception as exc:
            logger.warning("Telegram warm runtime teardown failed: %s", type(exc).__name__)
    try:
        from app.database import close_db

        await close_db()
    except Exception:
        pass


async def _ensure_telegram_runtime(config: dict[str, Any], generation: str) -> Any:
    """Return an initialized warm PTB app and reuse the open Mongo client."""
    global _telegram_application, _telegram_generation

    async with _runtime_lock():
        if _telegram_application is not None and _telegram_generation != generation:
            await _shutdown_cached_telegram_runtime()

        source_started = time.perf_counter()
        _download_source(generation)
        _apply_runtime_config(config)

        from app.database import connect_db

        await connect_db(max_retries=2, retry_delay=0.25, timeout_ms=6000, ensure_indexes=False)
        if _telegram_application is not None and _telegram_generation == generation:
            return _telegram_application

        from telegram.ext import Application
        from app.bot.handlers import register_handlers
        from app.photography.telegram import register_photography_handlers

        application = Application.builder().token(str(config["telegram_bot_token"])).build()
        register_photography_handlers(application)
        register_handlers(application)

        init_started = time.perf_counter()
        await application.initialize()
        _telegram_application = application
        _telegram_generation = generation
        logger.info(
            "Telegram v3.3 warm runtime ready generation=%s source_ms=%d init_ms=%d",
            generation[:12],
            int((init_started - source_started) * 1000),
            int((time.perf_counter() - init_started) * 1000),
        )
        return application
