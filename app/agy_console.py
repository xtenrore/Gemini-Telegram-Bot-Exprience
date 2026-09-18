"""Telegram bridge for the private Plane Alerts Antigravity worker.

Only the sole registered Telegram user (or ADMIN_TELEGRAM_ID when configured)
may open the bridge. Text entered while the bridge is active is forwarded to
AGY stdin; it is never executed as a host shell command.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import httpx
from telegram import Update
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.config import settings
from app.database import users_col

logger = logging.getLogger(__name__)

POLL_SECONDS = 1.0
MAX_TELEGRAM_CHUNK = 3500


@dataclass
class _Session:
    cursor: int = 0
    task: asyncio.Task[None] | None = None


_sessions: dict[int, _Session] = {}


def _configured() -> bool:
    return bool(settings.agy_worker_url.strip() and settings.agy_worker_token.strip())


def _url(path: str) -> str:
    return settings.agy_worker_url.rstrip("/") + path


def _headers() -> dict[str, str]:
    return {"X-AGY-Token": settings.agy_worker_token}


async def _authorized(user_id: int) -> bool:
    if settings.admin_telegram_id is not None:
        return user_id == settings.admin_telegram_id
    try:
        total = await users_col().count_documents({})
        if total != 1:
            return False
        only = await users_col().find_one({}, {"user_id": 1})
        return bool(only and int(only.get("user_id", -1)) == user_id)
    except Exception:
        logger.exception("Could not verify AGY console owner")
        return False


async def _request(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
    timeout = httpx.Timeout(15.0, connect=5.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.request(method, _url(path), headers=_headers(), **kwargs)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise RuntimeError("AGY worker returned an invalid response")
        return data


def _chunks(lines: list[str]) -> list[str]:
    out: list[str] = []
    current = ""
    for line in lines:
        line = line.replace("\x00", "").rstrip()
        if not line:
            continue
        candidate = line if not current else current + "\n" + line
        if len(candidate) > MAX_TELEGRAM_CHUNK:
            if current:
                out.append(current)
            while len(line) > MAX_TELEGRAM_CHUNK:
                out.append(line[:MAX_TELEGRAM_CHUNK])
                line = line[MAX_TELEGRAM_CHUNK:]
            current = line
        else:
            current = candidate
    if current:
        out.append(current)
    return out


async def _pump(user_id: int, chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = _sessions[user_id]
    consecutive_errors = 0
    while user_id in _sessions:
        try:
            data = await _request("GET", "/console/output", params={"cursor": session.cursor})
            session.cursor = int(data.get("cursor", session.cursor) or session.cursor)
            events = data.get("events", [])
            lines = [str(event.get("text", "")) for event in events if isinstance(event, dict)]
            for chunk in _chunks(lines):
                await context.bot.send_message(chat_id=chat_id, text=chunk)
            consecutive_errors = 0
            if not bool(data.get("running", False)):
                await context.bot.send_message(chat_id=chat_id, text="AGY console stopped. Send /agy to start it again.")
                _sessions.pop(user_id, None)
                return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            consecutive_errors += 1
            logger.warning("AGY console poll failed: %s", type(exc).__name__)
            if consecutive_errors == 1 or consecutive_errors % 15 == 0:
                try:
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text="AGY console connection is temporarily unavailable; retrying automatically.",
                    )
                except Exception:
                    pass
        await asyncio.sleep(POLL_SECONDS)


async def _drop_local_session(user_id: int) -> None:
    session = _sessions.pop(user_id, None)
    if session and session.task and not session.task.done():
        session.task.cancel()


async def cmd_agy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.message
    if user is None or message is None:
        return
    if not await _authorized(user.id):
        await message.reply_text("AGY console is restricted to the Plane Alerts owner.")
        return
    if not _configured():
        await message.reply_text("AGY worker is not configured yet.")
        return

    action = (context.args[0].lower() if context.args else "start")

    if action in {"stop", "exit", "close"}:
        try:
            await _request("POST", "/console/stop")
        finally:
            await _drop_local_session(user.id)
        await message.reply_text("AGY console stopped.")
        return

    if action == "status":
        try:
            worker = await _request("GET", "/supervisor/status")
            status = worker.get("status", "unknown")
            enabled = worker.get("enabled", False)
            running = worker.get("goal_running", False)
            next_run = worker.get("next_run_iso") or "not scheduled"
            await message.reply_text(
                "AGY Prediction Lab\n"
                f"Supervisor: {status}\n"
                f"Goal loop enabled: {enabled}\n"
                f"Goal running: {running}\n"
                f"Next run: {next_run}\n"
                "Paid credit overages: OFF\n"
                "Model: Gemini 3.1 Pro High"
            )
        except Exception as exc:
            logger.exception("AGY status failed")
            await message.reply_text(f"Could not read AGY status ({type(exc).__name__}).")
        return

    # Do not expose autonomous goal activation before the user finishes account
    # authentication and explicitly tells ChatGPT to enable it.
    if action == "goal":
        await message.reply_text(
            "The autonomous goal loop is intentionally locked until your AGY Google login is confirmed."
        )
        return

    # Repeating /agy must be idempotent. If this Telegram process is already
    # pumping the console, don't restart/re-attach it and don't repeat the long
    # connection banner. This also avoids resetting the output cursor.
    active = _sessions.get(user.id)
    if active is not None:
        try:
            state = await _request("GET", "/console/output", params={"cursor": active.cursor})
            if bool(state.get("running", False)):
                seconds = state.get("seconds_since_output")
                suffix = f" Last AGY output: {seconds}s ago." if seconds is not None else ""
                await message.reply_text(
                    "AGY console is already connected. I’m still forwarding Antigravity output here."
                    + suffix
                    + " Use /agy stop to disconnect."
                )
                return
        except Exception:
            logger.warning("Could not verify existing AGY Telegram session; reattaching")
        await _drop_local_session(user.id)

    try:
        data = await _request("POST", "/console/start")
    except Exception as exc:
        logger.exception("Could not start AGY console")
        await message.reply_text(f"Could not start AGY console ({type(exc).__name__}).")
        return

    session = _Session(cursor=0)
    _sessions[user.id] = session
    await message.reply_text(
        "AGY console connected. The Google sign-in URL and authorization-code prompt will appear here automatically. "
        "When Antigravity asks for the code, paste only that code into this chat. Use /agy stop to disconnect."
    )

    events = data.get("events", [])
    lines = [str(event.get("text", "")) for event in events if isinstance(event, dict)]
    session.cursor = int(data.get("cursor", 0) or 0)
    for chunk in _chunks(lines):
        await message.reply_text(chunk)
    session.task = asyncio.create_task(_pump(user.id, message.chat_id, context), name=f"agy-console-{user.id}")


async def handle_agy_text_if_active(update: Update) -> bool:
    """Forward free text to AGY when the owner has an active console."""
    user = update.effective_user
    message = update.message
    if user is None or message is None or message.text is None:
        return False
    if user.id not in _sessions:
        return False
    if not await _authorized(user.id):
        await _drop_local_session(user.id)
        return False
    try:
        await _request("POST", "/console/input", json={"text": message.text})
    except Exception as exc:
        logger.exception("Could not forward AGY console input")
        await message.reply_text(f"AGY input failed ({type(exc).__name__}). The console bridge will keep retrying output.")
    return True


async def _agy_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    if await handle_agy_text_if_active(update):
        # Prevent the normal Plane Alerts free-text handler from interpreting an
        # OAuth code or AGY prompt as an ICAO/radius message.
        raise ApplicationHandlerStop


def register_agy_console_handlers(app: Application) -> None:
    """Register the private console ahead of normal command/text handlers."""
    app.add_handler(CommandHandler("agy", cmd_agy), group=-20)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _agy_text_handler), group=-20)
