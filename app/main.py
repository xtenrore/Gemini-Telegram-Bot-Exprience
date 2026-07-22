"""Telegram Bot entry point using Long Polling.

This completely replaces the FastAPI/Webhook server, meaning we don't need
open ports, domains, or reverse proxies.
"""

from __future__ import annotations

import asyncio
import logging

from telegram.ext import Application

from app.aircraft.api_keys import opensky_key_manager
from app.aircraft.providers import close_http_client
from app.bot.handlers import register_handlers
from app.config import settings
from app.database import close_db, connect_db
from app.worker.monitor import init_services

logger = logging.getLogger(__name__)


async def main() -> None:
    """Run the bot in polling mode."""
    # Configure logging
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    )

    logger.info("Initializing Telegram bot (Long Polling)...")

    # Connect to MongoDB
    await connect_db()

    # Load OpenSky API keys
    key_count = opensky_key_manager.load_keys()
    logger.info("OpenSky key manager: %d key(s) available.", key_count)

    # Initialise AI and background services
    await init_services()

    # Build Telegram bot application
    app = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .build()
    )
    register_handlers(app)

    # First delete any existing webhooks so polling can work
    await app.bot.delete_webhook(drop_pending_updates=True)

    logger.info("Starting polling...")
    try:
        # This will block until the bot receives a stop signal (Ctrl+C)
        await app.initialize()
        await app.start()
        await app.updater.start_polling()

        # Keep the event loop running
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        pass
    finally:
        logger.info("Shutting down bot...")
        if app.updater:
            await app.updater.stop()
        await app.stop()
        await app.shutdown()
        await close_http_client()
        await close_db()
        logger.info("Shutdown complete.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
