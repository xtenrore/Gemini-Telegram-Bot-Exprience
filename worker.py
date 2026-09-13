"""Standalone background worker entry-point."""

from __future__ import annotations

import asyncio
import logging
import signal
import sys

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.aircraft.api_keys import opensky_key_manager
from app.aircraft.providers import close_http_client
from app.config import settings
from app.database import close_db, connect_db
from app.worker.monitor import init_services, run_monitor_cycle

logger = logging.getLogger(__name__)
_shutdown_event = asyncio.Event()


async def main() -> None:
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO), format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s")
    logger.info("Aircraft Monitor Worker starting ...")
    logger.info("Poll interval: %d seconds", settings.poll_interval_seconds)
    logger.info("Cooldown: %d minutes", settings.cooldown_minutes)
    logger.info("Default radius: %.0f km", settings.default_radius_km)
    try:
        await connect_db()
    except Exception as exc:
        logger.error("Failed to connect to MongoDB: %s. Exiting worker.", exc)
        return
    if not settings.slack_bot_token.strip():
        logger.warning("SLACK_BOT_TOKEN is not configured. Monitoring will run, but notifications cannot be delivered.")
    key_count = opensky_key_manager.load_keys()
    logger.info("OpenSky key manager: %d key(s) available.", key_count)
    await init_services()
    scheduler = AsyncIOScheduler()
    scheduler.add_job(run_monitor_cycle, trigger="interval", seconds=settings.poll_interval_seconds, id="aircraft_monitor", max_instances=1, coalesce=True)
    scheduler.start()
    logger.info("Scheduler started -- monitoring every %ds", settings.poll_interval_seconds)
    logger.info("Running initial monitor cycle ...")
    await run_monitor_cycle()
    await _shutdown_event.wait()
    logger.info("Shutting down worker ...")
    scheduler.shutdown(wait=False)
    await close_http_client()
    await close_db()
    logger.info("Worker shutdown complete.")


def _signal_handler(sig, frame) -> None:
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
