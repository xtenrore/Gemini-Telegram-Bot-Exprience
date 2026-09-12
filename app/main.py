"""Aircraft Alert Telegram Bot & Web Server Entry Point.

Unified entry point serving:
  - GET /          -> HTTP 200 "OK" (Render/UptimeRobot health checks)
  - GET /health    -> Detailed JSON health status (DB, Bot, Worker, Uptime)
  - GET /stats     -> JSON system statistics
  - POST /webhook  -> Telegram HTTPS Webhook receiver (when WEBHOOK_URL is configured)
  - /admin         -> Web Admin Dashboard & Management APIs
  - Long Polling   -> Automatically activated when WEBHOOK_URL is empty
"""

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
from telegram import Update
from telegram.ext import Application

from app.admin.routes import router as admin_router
from app.aircraft.api_keys import opensky_key_manager
from app.aircraft.providers import close_http_client
from app.bot.handlers import register_handlers
from app.config import settings
from app.database import close_db, connect_db, get_db, system_status_col, users_col
from app.worker.monitor import get_cycle_stats, init_services

logger = logging.getLogger(__name__)

# Global Telegram Application instance
telegram_app: Application | None = None
_server_start_time: float = time.time()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Lifespan context manager for startup and shutdown events."""
    global telegram_app  # noqa: PLW0603

    # Configure logging
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    )

    logger.info("Initializing Aircraft Alert Bot & Web Server...")

    # 1. Connect to MongoDB (try with short timeout, or continue connecting in background)
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
        logger.warning("MongoDB not reachable immediately: %s. Launching background reconnection loop...", exc)
        db_reconnect_task = asyncio.create_task(_reconnect_db_loop())

    # 2. Load OpenSky API keys
    key_count = opensky_key_manager.load_keys()
    logger.info("OpenSky key manager: %d key(s) available.", key_count)

    # 3. Initialise AI Judge and background services
    await init_services()

    # 4. Initialize Telegram Application (if token is configured)
    bot_token = settings.telegram_bot_token.strip()
    if bot_token and bot_token != "your_bot_token_from_botfather":
        try:
            telegram_app = (
                Application.builder()
                .token(bot_token)
                .build()
            )
            register_handlers(telegram_app)
            await telegram_app.initialize()
            await telegram_app.start()

            webhook_url = settings.webhook_url.strip()
            if webhook_url:
                full_webhook_url = f"{webhook_url.rstrip('/')}/webhook"
                logger.info("Registering Telegram Webhook: %s", full_webhook_url)
                await telegram_app.bot.set_webhook(
                    url=full_webhook_url,
                    secret_token=settings.webhook_secret if settings.webhook_secret else None,
                    drop_pending_updates=True,
                )
            else:
                logger.info("No WEBHOOK_URL configured — starting background long-polling...")
                await telegram_app.bot.delete_webhook(drop_pending_updates=True)
                await telegram_app.updater.start_polling(drop_pending_updates=True)
        except Exception as exc:
            logger.error("Failed to initialize Telegram bot: %s", exc)
    else:
        logger.warning(
            "TELEGRAM_BOT_TOKEN is not configured in .env! "
            "Web server running, but Telegram bot polling/webhook is disabled."
        )

    yield

    # Shutdown
    logger.info("Shutting down Web Server...")
    if telegram_app:
        try:
            if telegram_app.updater and telegram_app.updater.running:
                await telegram_app.updater.stop()
            await telegram_app.stop()
        except Exception as exc:
            logger.warning("Error stopping Telegram app: %s", exc)

    if db_reconnect_task and not db_reconnect_task.done():
        db_reconnect_task.cancel()

    await close_http_client()
    await close_db()
    logger.info("Web Server shutdown complete.")


# Create FastAPI application
app = FastAPI(
    title="Aircraft Alert Telegram Bot",
    description="Real-time ADS-B aircraft monitoring bot with Telegram integration & Admin dashboard",
    version="3.0.0",
    lifespan=lifespan,
)

# Enable CORS for admin dashboard
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include Admin panel router (mounted at /admin and /admin/api)
app.include_router(admin_router, prefix="/admin", tags=["admin"])


@app.api_route("/", methods=["GET", "HEAD"], response_class=Response)
async def root_health_check() -> Response:
    """Standard plain-text health check (HTTP 200 OK) for UptimeRobot and load balancers."""
    return Response(content="OK", media_type="text/plain", status_code=status.HTTP_200_OK)


@app.api_route("/health", methods=["GET", "HEAD"])
async def health_check() -> dict[str, Any]:
    """Detailed health check returning JSON status of database, bot, and worker."""
    db_ok = False
    try:
        db = get_db()
        await db.command("ping")
        db_ok = True
    except Exception:
        db_ok = False

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
                worker_info = {
                    "status": "active (in-process)",
                    "total_cycles": stats.get("total_cycles", 0),
                }
    except Exception:
        pass

    uptime = round(time.time() - _server_start_time, 1)

    return {
        "status": "healthy" if db_ok else "degraded",
        "uptime_seconds": uptime,
        "database_connected": db_ok,
        "bot_mode": bot_status,
        "worker": worker_info,
        "python_version": platform.python_version(),
    }


@app.api_route("/stats", methods=["GET", "HEAD"])
async def stats() -> dict[str, Any]:
    """Summary statistics about active users and monitoring cycles."""
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
    """Telegram HTTPS Webhook Receiver endpoint."""
    if not telegram_app:
        return Response(
            content="Bot not initialized",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    # Optional secret token verification
    if settings.webhook_secret:
        header_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
        if header_secret != settings.webhook_secret:
            return Response(
                content="Unauthorized",
                status_code=status.HTTP_401_UNAUTHORIZED,
            )

    try:
        data = await request.json()
        update = Update.de_json(data, telegram_app.bot)
        await telegram_app.process_update(update)
        return Response(status_code=status.HTTP_200_OK)
    except Exception as exc:
        logger.exception("Error processing webhook update: %s", exc)
        return Response(
            content="Internal Error",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", str(settings.port)))
    host = os.getenv("HOST", settings.host)
    logger.info("Starting Uvicorn server on %s:%d ...", host, port)
    uvicorn.run("app.main:app", host=host, port=port, reload=False)
