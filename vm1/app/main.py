"""VM 1 — Telegram Webhook & Web Server Entry Point.

Runs a FastAPI application serving:
  - GET /          -> HTTP 200 OK plain text "OK" (Render health check & UptimeRobot)
  - POST /webhook  -> Telegram HTTPS Webhook receiver endpoint
  - /admin         -> Admin dashboard & status APIs
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from telegram import Update
from telegram.ext import Application

from app.aircraft.api_keys import opensky_key_manager
from app.aircraft.providers import close_http_client
from app.admin.routes import admin_router
from app.bot.handlers import register_handlers
from app.config import settings
from app.database import close_db, connect_db

logger = logging.getLogger(__name__)

# Global Telegram Application instance
telegram_app: Application | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Lifespan context manager for startup and shutdown events."""
    global telegram_app  # noqa: PLW0603

    # Configure logging
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    )

    logger.info("Initializing VM 1 (Telegram Webhook & Web Server)...")

    # Connect to MongoDB
    await connect_db()

    # Load OpenSky API keys
    key_count = opensky_key_manager.load_keys()
    logger.info("OpenSky key manager: %d key(s) available.", key_count)

    # Initialize Telegram Application
    telegram_app = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .build()
    )
    register_handlers(telegram_app)
    await telegram_app.initialize()
    await telegram_app.start()

    # Register Webhook if WEBHOOK_URL is set, otherwise fall back to background long-polling
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
        logger.info("No WEBHOOK_URL configured — starting local background long-polling...")
        await telegram_app.bot.delete_webhook(drop_pending_updates=True)
        await telegram_app.updater.start_polling(drop_pending_updates=True)

    yield

    # Shutdown
    logger.info("Shutting down VM 1 Web Server...")
    if telegram_app:
        if telegram_app.updater and telegram_app.updater.running:
            await telegram_app.updater.stop()
        await telegram_app.stop()
        await telegram_app.shutdown()

    await close_http_client()
    await close_db()
    logger.info("VM 1 Shutdown complete.")


# Create FastAPI application
app = FastAPI(
    title="Telegram Aircraft Alert Bot — VM 1 Web Server",
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

# Include Admin panel router
app.include_router(admin_router, prefix="/admin", tags=["admin"])


@app.get("/", response_class=Response)
async def root_health_check() -> Response:
    """Render deployment health check & UptimeRobot monitoring endpoint."""
    return Response(content="OK", media_type="text/plain", status_code=status.HTTP_200_OK)


@app.post("/webhook")
async def telegram_webhook(request: Request) -> Response:
    """Telegram HTTPS Webhook Receiver endpoint."""
    if not telegram_app:
        return Response(status_code=status.HTTP_503_SERVICE_UNAVAILABLE)

    # Optional secret token verification
    if settings.webhook_secret:
        header_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
        if header_secret != settings.webhook_secret:
            return Response(status_code=status.HTTP_401_UNAUTHORIZED)

    try:
        data = await request.json()
        update = Update.de_json(data, telegram_app.bot)
        await telegram_app.process_update(update)
        return Response(status_code=status.HTTP_200_OK)
    except Exception as exc:
        logger.exception("Error processing webhook update: %s", exc)
        return Response(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)
