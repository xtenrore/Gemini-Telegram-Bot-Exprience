"""Standalone background worker entry-point.

Runs the ADS-B monitor and, when configured for polling mode, the Telegram bot
in the same persistent process. This avoids trying to keep Telegram long
polling alive inside a Vercel serverless function.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import BotCommand
from telegram.ext import Application

from app.aircraft.api_keys import opensky_key_manager
from app.aircraft.providers import close_http_client
from app.bot.handlers import register_handlers
from app.config import settings
from app.database import close_db, connect_db
from app.photography.telegram import register_photography_handlers
from app.worker.monitor import init_services, run_monitor_cycle

logger = logging.getLogger(__name__)

_shutdown_event = asyncio.Event()


async def _start_telegram_polling() -> Application | None:
    """Start Telegram long polling in the persistent worker process."""
    bot_token = settings.telegram_bot_token.strip()
    if not bot_token or bot_token == "your_bot_token_from_botfather":
        logger.warning("TELEGRAM_BOT_TOKEN is not configured; polling disabled.")
        return None

    app = Application.builder().token(bot_token).build()
    register_handlers(app)
    register_photography_handlers(app)

    await app.initialize()
    await app.start()
    await app.bot.delete_webhook(drop_pending_updates=True)

    try:
        await app.bot.set_my_commands(
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
        logger.warning("Could not update Telegram command menu: %s", type(exc).__name__)

    if app.updater is None:
        raise RuntimeError("Telegram updater is unavailable; cannot start long polling")

    await app.updater.start_polling(drop_pending_updates=True)
    logger.info("Telegram long polling started in persistent worker.")
    return app


async def _stop_telegram(app: Application | None) -> None:
    if app is None:
        return
    try:
        if app.updater and app.updater.running:
            await app.updater.stop()
        await app.stop()
        await app.shutdown()
    except Exception as exc:
        logger.warning("Error stopping Telegram polling: %s", type(exc).__name__)


async def main() -> None:
    """Worker main loop."""
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    )
    logger.info("Aircraft Monitor Worker starting ...")
    logger.info("Poll interval: %d seconds", settings.poll_interval_seconds)
    logger.info("Cooldown: %d minutes", settings.cooldown_minutes)
    logger.info("Default radius: %.0f km", settings.default_radius_km)

    try:
        await connect_db()
    except Exception as exc:
        logger.error("Failed to connect to MongoDB: %s. Exiting worker.", type(exc).__name__)
        return

    key_count = opensky_key_manager.load_keys()
    logger.info("OpenSky key manager: %d key(s) available.", key_count)
    await init_services()

    telegram_app: Application | None = None
    try:
        telegram_app = await _start_telegram_polling()
    except Exception:
        logger.exception("Failed to start Telegram long polling")
        await close_http_client()
        await close_db()
        return

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        run_monitor_cycle,
        trigger="interval",
        seconds=settings.poll_interval_seconds,
        id="aircraft_monitor",
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    logger.info("Scheduler started -- monitoring every %ds", settings.poll_interval_seconds)

    logger.info("Running initial monitor cycle ...")
    await run_monitor_cycle()

    await _shutdown_event.wait()

    logger.info("Shutting down worker ...")
    scheduler.shutdown(wait=False)
    await _stop_telegram(telegram_app)
    await close_http_client()
    await close_db()
    logger.info("Worker shutdown complete.")


def _signal_handler(sig, frame) -> None:
    """Handle SIGINT / SIGTERM for graceful shutdown."""
    logger.info("Received signal %s -- initiating shutdown ...", sig)
    _shutdown_event.set()


if __name__ == "__main__":
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    finally:
        logger.info("Worker process exited.")
        sys.exit(0)
