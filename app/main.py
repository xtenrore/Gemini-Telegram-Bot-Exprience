"""Plane? v3.5 aircraft spotting intelligence, Telegram bot, and web server."""
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
from app.logging_security import configure_secure_logging
from app.photography.telegram import register_photography_handlers
from app.worker.monitor import get_cycle_stats, init_services
from app.worker.v35 import run_monitor_cycle_v35 as run_monitor_cycle

logger = logging.getLogger(__name__)
telegram_app: Application | None = None
_server_start_time: float = time.time()


async def _monitor_loop() -> None:
    """Run the ADS-B monitor in-process to fit small container memory limits."""
    logger.info(
        "Integrated ADS-B worker enabled: base interval=%ds, v3.5 adaptive shared polling active",
        settings.poll_interval_seconds,
    )
    first_cycle_confirmed = False
    while True:
        cycle_started = time.monotonic()
        try:
            # get_db raises until the Atlas connection has been established.
            get_db()
            await run_monitor_cycle()
            if not first_cycle_confirmed:
                stats = get_cycle_stats()
                if int(stats.get("total_cycles", 0) or 0) > 0:
                    logger.info(
                        "Integrated ADS-B worker first cycle completed: total_cycles=%s duration_ms=%s",
                        stats.get("total_cycles"),
                        stats.get("last_cycle_duration_ms"),
                    )
                    first_cycle_confirmed = True
        except asyncio.CancelledError:
            raise
        except RuntimeError:
            logger.debug("Integrated worker waiting for MongoDB connection")
        except Exception:
            logger.exception("Integrated ADS-B monitor iteration failed")

        elapsed = time.monotonic() - cycle_started
        delay = max(0.25, float(settings.poll_interval_seconds) - elapsed)
        await asyncio.sleep(delay)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    global telegram_app

    configure_secure_logging()
    logger.info("Initializing Plane? v3.5 Spotting Intelligence (shared adaptive ADS-B polling + deterministic photography core)...")

    db_reconnect_task: asyncio.Task | None = None
    monitor_task: asyncio.Task | None = None

    async def _reconnect_db_loop() -> None:
        while True:
            try:
                await connect_db(max_retries=1, retry_delay=1.0, timeout_ms=10000)
                logger.info("MongoDB background connection established.")
                break
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("MongoDB not ready yet (%s). Retrying in 5s...", type(exc).__name__)
                await asyncio.sleep(5)

    try:
        await connect_db(max_retries=2, retry_delay=1.0, timeout_ms=10000)
    except Exception as exc:
        logger.warning("MongoDB not reachable immediately: %s. Launching reconnect loop...", type(exc).__name__)
        db_reconnect_task = asyncio.create_task(_reconnect_db_loop(), name="mongo-reconnect")

    key_count = opensky_key_manager.load_keys()
    logger.info("OpenSky key manager: %d key(s) available.", key_count)
    await init_services()

    # Keep monitoring in the same Python process so the production service has a
    # single bounded-memory runtime for the API, Telegram, and spotting worker.
    monitor_task = asyncio.create_task(_monitor_loop(), name="aircraft-monitor")

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
                        BotCommand("spotting", "Open Spotting Mode"),
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

    logger.info("Shutting down Plane? v3.5...")
    if telegram_app:
        try:
            if telegram_app.updater and telegram_app.updater.running:
                await telegram_app.updater.stop()
            await telegram_app.stop()
            await telegram_app.shutdown()
        except Exception as exc:
            logger.warning("Error stopping Telegram app: %s", exc)

    for task in (monitor_task, db_reconnect_task):
        if task and not task.done():
            task.cancel()
    for task in (monitor_task, db_reconnect_task):
        if task:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("Background task failed during shutdown")

    await close_http_client()
    await close_db()


app = FastAPI(
    title="Plane? Spotting Intelligence",
    description="Deterministic real-time ADS-B spotting intelligence with optional Gemini enhancement",
    version="3.5.0",
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
                "version": doc.get("plane_version", "3.5.0"),
                "total_cycles": doc.get("total_cycles", 0),
                "last_cycle_duration_ms": doc.get("last_cycle_duration_ms", 0.0),
                "seconds_since_last_cycle": round(time.time() - last_time, 1),
                "polling_mode": doc.get("polling_mode", "adaptive-shared-regions"),
                "shared_regions_last_cycle": doc.get("shared_regions_last_cycle", 0),
                "provider_queries_last_cycle": doc.get("provider_queries_last_cycle", 0),
                "shared_snapshot_cache_hits_last_cycle": doc.get("shared_snapshot_cache_hits_last_cycle", 0),
            }
        else:
            stats = get_cycle_stats()
            if stats.get("total_cycles", 0) > 0:
                worker_info = {"status": "active (in-process)", "version": "3.5.0", "total_cycles": stats.get("total_cycles", 0)}
    except Exception:
        pass

    return {
        "status": "healthy" if db_ok else "degraded",
        "version": "3.5.0",
        "database_connected": db_ok,
        "bot_mode": bot_status,
        "uptime_seconds": round(time.time() - _server_start_time, 1),
        "worker": worker_info,
        "spotting_intelligence": {
            "deterministic_core": True,
            "gemini_advisor_enabled": bool(settings.gemini_api_key.strip()),
            "weather_provider": "Open-Meteo",
            "shared_adaptive_adsb_polling": True,
        },
        "python_version": platform.python_version(),
    }


@app.api_route("/stats", methods=["GET", "HEAD"])
async def stats() -> dict[str, Any]:
    active_users = 0
    total_users = 0
    worker_metrics: dict[str, Any] = {}
    try:
        active_users = await users_col().count_documents({"setup_complete": True})
        total_users = await users_col().count_documents({})
        doc = await system_status_col().find_one({"_id": "monitor_worker"})
        if doc:
            worker_metrics = {
                "shared_regions_last_cycle": doc.get("shared_regions_last_cycle", 0),
                "provider_queries_last_cycle": doc.get("provider_queries_last_cycle", 0),
                "shared_snapshot_cache_hits_last_cycle": doc.get("shared_snapshot_cache_hits_last_cycle", 0),
                "polling_mode": doc.get("polling_mode", "adaptive-shared-regions"),
            }
    except Exception:
        pass
    return {
        "version": "3.5.0",
        "active_users": active_users,
        "total_users": total_users,
        "poll_interval_seconds": settings.poll_interval_seconds,
        "base_monitor_interval_seconds": settings.poll_interval_seconds,
        "discovery_poll_interval_seconds": 15,
        "hot_region_poll_interval_seconds": 5,
        "default_radius_km": settings.default_radius_km,
        "cooldown_minutes": settings.cooldown_minutes,
        "cycle_stats": get_cycle_stats(),
        "shared_polling": worker_metrics,
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