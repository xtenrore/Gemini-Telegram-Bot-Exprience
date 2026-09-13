"""Aircraft Alert Telegram Bot + v3.2 photography assistant + web server."""
from __future__ import annotations

import asyncio
import logging
import os
import platform
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

from fastapi import FastAPI, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from telegram import BotCommand, Update
from telegram.ext import Application

from app.admin.routes import router as admin_router
from app.aircraft.api_keys import opensky_key_manager
from app.aircraft.providers import close_http_client
from app.bot.handlers import register_handlers
from app.config import settings
from app.database import close_db, connect_db, get_db, system_status_col, users_col
from app.photography.telegram import register_photography_handlers
from app.worker.monitor import get_cycle_stats, init_services

logger = logging.getLogger(__name__)
telegram_app: Application | None = None
_server_start_time: float = time.time()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    global telegram_app

    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    )
    logger.info("Initializing Aircraft Alert v3.2 (Telegram + Gemini Photography)...")

    db_reconnect_task = None

    async def _reconnect_db_loop() -> None:
        while True:
            try:
                await connect_db(max_retries=1, retry_delay=1.0, timeout_ms=2000)
                logger.info("MongoDB background connection established.")
                break
            except Exception as exc:
                logger.warning("MongoDB not ready yet (%s). Retrying in 3s...", exc)
                await asyncio.sleep(3)

    try:
        await connect_db(max_retries=1, retry_delay=0.5, timeout_ms=1000)
    except Exception as exc:
        logger.warning("MongoDB not reachable immediately: %s. Launching reconnect loop...", exc)
        db_reconnect_task = asyncio.create_task(_reconnect_db_loop())

    key_count = opensky_key_manager.load_keys()
    logger.info("OpenSky key manager: %d key(s) available.", key_count)
    await init_services()

    bot_token = settings.telegram_bot_token.strip()
    if bot_token and bot_token != "your_bot_token_from_botfather":
        try:
            telegram_app = Application.builder().token(bot_token).build()
            register_handlers(telegram_app)
            register_photography_handlers(telegram_app)
            await telegram_app.initialize()
            await telegram_app.start()
            try:
                await telegram_app.bot.set_my_commands(
                    [
                        BotCommand("start", "Set up aircraft alerts"),
                        BotCommand("status", "Show monitoring status"),
                        BotCommand("location", "Set monitoring / shooting location"),
                        BotCommand("preferences", "Choose aircraft types"),
                        BotCommand("camera", "Set your camera body"),
                        BotCommand("lens", "Set your aircraft lens"),
                        BotCommand("photo", "Get live best-shot camera settings"),
                        BotCommand("conditions", "Show weather / sun / haze conditions"),
                        BotCommand("help", "Show all commands"),
                    ]
                )
            except Exception as exc:
                logger.warning("Could not update Telegram command menu: %s", exc)

            webhook_url = settings.webhook_url.strip()
            if webhook_url:
                full_webhook_url = f"{webhook_url.rstrip('/')}/webhook"
                logger.info("Registering Telegram webhook: %s", full_webhook_url)
                await telegram_app.bot.set_webhook(
                    url=full_webhook_url,
                    secret_token=settings.webhook_secret if settings.webhook_secret else None,
                    drop_pending_updates=True,
                )
            else:
                logger.info("Starting Telegram long polling.")
                await telegram_app.bot.delete_webhook(drop_pending_updates=True)
                if telegram_app.updater:
                    await telegram_app.updater.start_polling(drop_pending_updates=True)
        except Exception as exc:
            logger.exception("Failed to initialize Telegram bot: %s", exc)
    else:
        logger.warning("TELEGRAM_BOT_TOKEN is not configured; Telegram runtime disabled.")

    yield

    logger.info("Shutting down Aircraft Alert v3.2...")
    if telegram_app:
        try:
            if telegram_app.updater and telegram_app.updater.running:
                await telegram_app.updater.stop()
            await telegram_app.stop()
            await telegram_app.shutdown()
        except Exception as exc:
            logger.warning("Error stopping Telegram app: %s", exc)

    if db_reconnect_task and not db_reconnect_task.done():
        db_reconnect_task.cancel()
    await close_http_client()
    await close_db()


app = FastAPI(
    title="Aircraft Alert Telegram Bot",
    description="Real-time ADS-B monitoring with Gemini-powered aviation photography guidance",
    version="3.2.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(admin_router, prefix="/admin", tags=["admin"])


@app.api_route("/", methods=["GET", "HEAD"], response_class=Response)
async def root_health_check() -> Response:
    return Response(content="OK", media_type="text/plain", status_code=status.HTTP_200_OK)


@app.api_route("/health", methods=["GET", "HEAD"])
async def health_check() -> dict[str, Any]:
    db_ok = False
    try:
        db = get_db()
        await db.command("ping")
        db_ok = True
    except Exception:
        pass

    bot_status = "unconfigured"
    if settings.telegram_bot_token and settings.telegram_bot_token != "your_bot_token_from_botfather":
        if telegram_app and telegram_app.running:
            bot_status = "webhook" if settings.webhook_url.strip() else "polling"
        else:
            bot_status = "stopped"

    worker_info: dict[str, Any] = {"status": "unknown"}
    try:
        doc = await system_status_col().find_one({"_id": "monitor_worker"})
        if doc:
            last_time = doc.get("last_cycle_time", 0.0)
            is_stale = (time.time() - last_time) > (settings.poll_interval_seconds * 4)
            worker_info = {
                "status": "active" if not is_stale else "stale",
                "total_cycles": doc.get("total_cycles", 0),
                "last_cycle_duration_ms": doc.get("last_cycle_duration_ms", 0.0),
                "seconds_since_last_cycle": round(time.time() - last_time, 1),
            }
        else:
            stats = get_cycle_stats()
            if stats.get("total_cycles", 0) > 0:
                worker_info = {"status": "active (in-process)", "total_cycles": stats.get("total_cycles", 0)}
    except Exception:
        pass

    return {
        "status": "healthy" if db_ok else "degraded",
        "version": "3.2.0",
        "uptime_seconds": round(time.time() - _server_start_time, 1),
        "database_connected": db_ok,
        "bot_mode": bot_status,
        "worker": worker_info,
        "photography": {
            "gemini_enabled": bool(settings.gemini_api_key.strip()),
            "model": settings.gemini_photo_model,
            "weather_provider": "Open-Meteo",
        },
        "python_version": platform.python_version(),
    }


@app.api_route("/stats", methods=["GET", "HEAD"])
async def stats() -> dict[str, Any]:
    active_users = 0
    total_users = 0
    try:
        active_users = await users_col().count_documents({"setup_complete": True})
        total_users = await users_col().count_documents({})
    except Exception:
        pass
    return {
        "active_users": active_users,
        "total_users": total_users,
        "poll_interval_seconds": settings.poll_interval_seconds,
        "default_radius_km": settings.default_radius_km,
        "cooldown_minutes": settings.cooldown_minutes,
        "cycle_stats": get_cycle_stats(),
    }


@app.post("/webhook")
async def telegram_webhook(request: Request) -> Response:
    if not telegram_app:
        return Response(content="Bot not initialized", status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
    if settings.webhook_secret:
        if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != settings.webhook_secret:
            return Response(content="Unauthorized", status_code=status.HTTP_401_UNAUTHORIZED)
    try:
        data = await request.json()
        update = Update.de_json(data, telegram_app.bot)
        await telegram_app.process_update(update)
        return Response(status_code=status.HTTP_200_OK)
    except Exception as exc:
        logger.exception("Error processing Telegram webhook: %s", exc)
        return Response(content="Internal Error", status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", str(settings.port)))
    host = os.getenv("HOST", settings.host)
    uvicorn.run("app.main:app", host=host, port=port, reload=False)
