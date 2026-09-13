"""Aircraft Alert Slack bot and FastAPI admin/health server."""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

from fastapi import FastAPI, Response, status
from fastapi.middleware.cors import CORSMiddleware

from app.admin.routes import router as admin_router
from app.aircraft.api_keys import opensky_key_manager
from app.aircraft.providers import close_http_client
from app.config import settings
from app.database import close_db, connect_db, get_db, system_status_col, users_col
from app.slack_app import slack_configured, start_slack_socket, stop_slack_socket
from app.worker.monitor import get_cycle_stats, init_services

logger = logging.getLogger(__name__)
_server_start_time: float = time.time()
_slack_task: asyncio.Task[Any] | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    global _slack_task
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO), format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s")
    logger.info("Initializing Aircraft Alert Slack Bot & Web Server...")
    db_reconnect_task: asyncio.Task[Any] | None = None

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

    key_count = opensky_key_manager.load_keys()
    logger.info("OpenSky key manager: %d key(s) available.", key_count)
    await init_services()
    try:
        _slack_task = await start_slack_socket()
    except Exception:
        logger.exception("Failed to start Slack Socket Mode")
        _slack_task = None

    yield

    logger.info("Shutting down Web Server...")
    await stop_slack_socket()
    if db_reconnect_task and not db_reconnect_task.done():
        db_reconnect_task.cancel()
    await close_http_client()
    await close_db()
    logger.info("Web Server shutdown complete.")


app = FastAPI(title="Aircraft Alert Slack Bot", description="Real-time ADS-B aircraft monitoring with Slack Socket Mode and admin dashboard", version="3.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
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
    if not slack_configured():
        bot_status = "unconfigured"
    elif _slack_task is None:
        bot_status = "stopped"
    elif _slack_task.done():
        bot_status = "failed"
    else:
        bot_status = "socket"
    worker_info: dict[str, Any] = {"status": "unknown"}
    try:
        doc = await system_status_col().find_one({"_id": "monitor_worker"})
        if doc:
            last_time = doc.get("last_cycle_time", 0.0)
            is_stale = (time.time() - last_time) > (settings.poll_interval_seconds * 4)
            worker_info = {"status": "active" if not is_stale else "stale", "total_cycles": doc.get("total_cycles", 0), "last_cycle_duration_ms": doc.get("last_cycle_duration_ms", 0.0), "seconds_since_last_cycle": round(time.time() - last_time, 1)}
        else:
            stats = get_cycle_stats()
            if stats.get("total_cycles", 0) > 0:
                worker_info = {"status": "active (in-process)", "total_cycles": stats.get("total_cycles", 0)}
    except Exception:
        pass
    return {"status": "healthy" if db_ok and bot_status == "socket" else "degraded", "uptime_seconds": round(time.time() - _server_start_time, 1), "database_connected": db_ok, "bot_mode": bot_status, "worker": worker_info, "python_version": platform.python_version()}


@app.api_route("/stats", methods=["GET", "HEAD"])
async def stats() -> dict[str, Any]:
    active_users = 0
    total_users = 0
    try:
        active_users = await users_col().count_documents({"platform": "slack", "setup_complete": True})
        total_users = await users_col().count_documents({"platform": "slack"})
    except Exception:
        pass
    return {"active_users": active_users, "total_users": total_users, "poll_interval_seconds": settings.poll_interval_seconds, "default_radius_km": settings.default_radius_km, "cooldown_minutes": settings.cooldown_minutes, "cycle_stats": get_cycle_stats()}


@app.post("/webhook")
async def legacy_telegram_webhook() -> Response:
    return Response(content="Telegram integration removed; Slack Socket Mode is active.", media_type="text/plain", status_code=status.HTTP_410_GONE)


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", str(settings.port)))
    host = os.getenv("HOST", settings.host)
    uvicorn.run("app.main:app", host=host, port=port, reload=False)
