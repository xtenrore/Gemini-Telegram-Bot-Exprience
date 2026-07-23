"""Standalone background worker entry-point.

It uses APScheduler to poll aircraft data at a fixed interval and send
notifications to matching users.

Usage:
    python worker.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.aircraft.api_keys import opensky_key_manager
from app.aircraft.providers import close_http_client
from app.config import settings
from app.database import close_db, connect_db
from app.worker.monitor import init_services, run_monitor_cycle

logger = logging.getLogger(__name__)

# Flag for graceful shutdown
_shutdown_event = asyncio.Event()

async def _dummy_health_handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """Handle dummy HTTP requests for Render health checks."""
    try:
        # Read the request line
        request_line = await asyncio.wait_for(reader.readline(), timeout=2.0)
        if request_line:
            response = b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nConnection: close\r\n\r\nOK"
            writer.write(response)
            await writer.drain()
    except Exception:
        pass
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


async def main() -> None:
    """Worker main loop."""
    # Configure logging
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    )
    logger.info("Aircraft Monitor Worker starting ...")
    logger.info("Poll interval: %d seconds", settings.poll_interval_seconds)
    logger.info("Cooldown: %d minutes", settings.cooldown_minutes)
    logger.info("Default radius: %.0f km", settings.default_radius_km)

    # Connect to MongoDB
    await connect_db()

    # Load OpenSky API keys
    key_count = opensky_key_manager.load_keys()
    logger.info("OpenSky key manager: %d key(s) available.", key_count)

    # Initialise AI and background services
    await init_services()

    # Set up the scheduler
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        run_monitor_cycle,
        trigger="interval",
        seconds=settings.poll_interval_seconds,
        id="aircraft_monitor",
        max_instances=1,  # Don't overlap if a cycle runs long
        coalesce=True,    # Skip missed runs
    )
    
    # Start dummy web server for Render health checks
    port = int(os.environ.get("PORT", "10000"))
    dummy_server = await asyncio.start_server(_dummy_health_handler, "0.0.0.0", port)
    logger.info("Started dummy health check server on port %d", port)
    
    scheduler.start()
    logger.info("Scheduler started -- monitoring every %ds", settings.poll_interval_seconds)

    # Run an initial cycle immediately
    logger.info("Running initial monitor cycle ...")
    await run_monitor_cycle()

    # Wait until shutdown signal
    await _shutdown_event.wait()

    # Cleanup
    logger.info("Shutting down worker ...")
    dummy_server.close()
    await dummy_server.wait_closed()
    scheduler.shutdown(wait=False)
    await close_http_client()
    await close_db()
    logger.info("Worker shutdown complete.")


def _signal_handler(sig, frame) -> None:
    """Handle SIGINT / SIGTERM for graceful shutdown."""
    logger.info("Received signal %s -- initiating shutdown ...", sig)
    _shutdown_event.set()


if __name__ == "__main__":
    # Register signal handlers
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    finally:
        logger.info("Worker process exited.")
        sys.exit(0)
