"""Telegram bridge for the private Plane Alerts Antigravity worker.

Only the sole registered Telegram user (or ADMIN_TELEGRAM_ID when configured)
may open the bridge. Text entered while the bridge is active is forwarded to
AGY stdin; it is never executed as a host shell command.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.agy_state import set_agy_console_active
from app.config import settings
from app.database import users_col

logger = logging.getLogger(__name__)

POLL_SECONDS = 1.0
MAX_TELEGRAM_CHUNK = 3500

# Telegram cannot send a blank message, so a terminal UI waiting for a bare
# Enter key would otherwise be impossible to operate from the chat. Keep this
# mapping deliberately tiny: these are terminal keystrokes, never shell text.
TERMINAL_CONTROLS: dict[str, str] = {
    "enter": "\r",
}

# Antigravity renders OAuth URLs using terminal hyperlink escape sequences.
# The worker strips control bytes for Telegram, which can leave terminal markup
# around the URL. Extract the HTTPS target and present it separately as a real
# Telegram URL button instead of making the user copy terminal-rendered text.
_URL_RE = re.compile(r"https://[^\s<>\"']+")
_GOOGLE_HOST_SUFFIXES = (".google.com", ".googleusercontent.com")


@dataclass
class _Session:
    cursor: int = 0
    task: asyncio.Task[None] | None = None
    seen_urls: set[str] = field(default_factory=set)


_sessions: dict[int, _Session] = {}


def _configured() -> bool:
    return bool(settings.agy_worker_url.strip() and settings.agy_worker_token.strip())


def _url(path: str) -> str:
    return settings.agy_worker_url.rstrip("/") + path


def _headers() -> dict[str, str]:
    return {"X-AGY-Token": settings.agy_worker_token}


def _controls_keyboard(oauth_url: str | None = None) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if oauth_url:
        rows.append([InlineKeyboardButton("🔐 Open Google sign-in", url=oauth_url)])
    rows.append([InlineKeyboardButton("↵ Enter", callback_data="agy:key:enter")])
    return InlineKeyboardMarkup(rows)


def _sanitize_google_url(candidate: str) -> str | None:
    value = candidate.strip()
    # OSC-8 hyperlinks may leave a closing `8;;` marker once terminal control
    # bytes have been removed. It is never part of Google's OAuth URL.
    for marker in ("8;;", "]8;;", "\x1b", "\x07"):
        if marker in value:
            value = value.split(marker, 1)[0]
    value = value.rstrip(".,);]}>")
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https":
        return None
    if not (host == "google.com" or host.endswith(_GOOGLE_HOST_SUFFIXES)):
        return None
    return value


def _extract_google_urls(lines: list[str]) -> list[str]:
    found: list[str] = []
    # First inspect individual lines; then inspect the joined text in case a TUI
    # split visual output across line events.
    candidates = list(lines)
    if len(lines) > 1:
        candidates.append("".join(lines))
    for text in candidates:
        for match in _URL_RE.findall(text):
            cleaned = _sanitize_google_url(match)
            if cleaned and cleaned not in found:
                found.append(cleaned)
    return found


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


async def _send_console_lines(
    session: _Session,
    chat_id: int,
    bot: Any,
    lines: list[str],
) -> None:
    urls = _extract_google_urls(lines)
    for oauth_url in urls:
        if oauth_url in session.seen_urls:
            continue
        session.seen_urls.add(oauth_url)
        await bot.send_message(
            chat_id=chat_id,
            text=(
                "🔐 Google sign-in\n\n"
                "Tap the button below to open the clean Google OAuth page. "
                "After Google gives you an authorization code, send it as:\n\n"
                "/agy code YOUR_CODE"
            ),
            reply_markup=_controls_keyboard(oauth_url),
        )

    # Do not repeat the terminal-rendered/corrupted version of a Google URL once
    # we have extracted it into a proper Telegram button.
    display_lines = [line for line in lines if not _extract_google_urls([line])]
    for chunk in _chunks(display_lines):
        await bot.send_message(chat_id=chat_id, text=chunk, reply_markup=_controls_keyboard())


async def _pump(user_id: int, chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = _sessions[user_id]
    consecutive_errors = 0
    while user_id in _sessions:
        try:
            data = await _request("GET", "/console/output", params={"cursor": session.cursor})
            session.cursor = int(data.get("cursor", session.cursor) or session.cursor)
            events = data.get("events", [])
            lines = [str(event.get("text", "")) for event in events if isinstance(event, dict)]
            await _send_console_lines(session, chat_id, context.bot, lines)
            consecutive_errors = 0
            if not bool(data.get("running", False)):
                await context.bot.send_message(chat_id=chat_id, text="AGY console stopped. Plane Alerts notifications resumed.")
                _sessions.pop(user_id, None)
                set_agy_console_active(user_id, False)
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
    set_agy_console_active(user_id, False)
    if session and session.task and not session.task.done():
        session.task.cancel()


async def _send_terminal_control(user_id: int, key: str) -> None:
    if user_id not in _sessions:
        raise RuntimeError("AGY console is not connected")
    payload = TERMINAL_CONTROLS.get(key)
    if payload is None:
        raise ValueError(f"Unsupported terminal control: {key}")
    await _request("POST", "/console/input", json={"text": payload})


async def _send_code(user_id: int, code: str) -> None:
    if user_id not in _sessions:
        raise RuntimeError("AGY console is not connected")
    code = code.strip()
    if not code:
        raise ValueError("Authorization code is empty")
    # The worker appends the terminal newline; only the code itself is sent.
    await _request("POST", "/console/input", json={"text": code})


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

    if action == "code":
        code = " ".join(context.args[1:]).strip()
        if not code:
            await message.reply_text("Use: /agy code YOUR_AUTHORIZATION_CODE")
            return
        try:
            await _send_code(user.id, code)
            await message.reply_text("Authorization code sent to Antigravity.", reply_markup=_controls_keyboard())
        except RuntimeError:
            await message.reply_text("AGY console is not connected. Send /agy first.")
        except Exception as exc:
            logger.exception("Could not send AGY authorization code")
            await message.reply_text(f"Could not send authorization code ({type(exc).__name__}).")
        return

    if action in TERMINAL_CONTROLS:
        try:
            await _send_terminal_control(user.id, action)
            await message.reply_text("↵ Enter sent to Antigravity.", reply_markup=_controls_keyboard())
        except RuntimeError:
            await message.reply_text("AGY console is not connected. Send /agy first.")
        except Exception as exc:
            logger.exception("Could not send AGY terminal control")
            await message.reply_text(f"Could not send Enter ({type(exc).__name__}).")
        return

    if action in {"stop", "exit", "close"}:
        try:
            await _request("POST", "/console/stop")
        finally:
            await _drop_local_session(user.id)
        await message.reply_text("AGY console stopped. Plane Alerts notifications resumed.")
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

    active = _sessions.get(user.id)
    if active is not None:
        try:
            state = await _request("GET", "/console/output", params={"cursor": active.cursor})
            if bool(state.get("running", False)):
                set_agy_console_active(user.id, True)
                seconds = state.get("seconds_since_output")
                suffix = f" Last AGY output: {seconds}s ago." if seconds is not None else ""
                await message.reply_text(
                    "AGY console is already connected. Plane Alerts notifications are muted while you are here."
                    + suffix
                    + " Use /agy stop to disconnect.",
                    reply_markup=_controls_keyboard(),
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
    set_agy_console_active(user.id, True)
    await message.reply_text(
        "AGY console connected. Plane Alerts notifications are muted until /agy stop. "
        "Use ↵ Enter for terminal Enter. I’ll send the Google OAuth URL separately as a clean button. "
        "When Google gives you the code, send /agy code YOUR_CODE.",
        reply_markup=_controls_keyboard(),
    )

    events = data.get("events", [])
    lines = [str(event.get("text", "")) for event in events if isinstance(event, dict)]
    session.cursor = int(data.get("cursor", 0) or 0)
    await _send_console_lines(session, message.chat_id, context.bot, lines)
    session.task = asyncio.create_task(_pump(user.id, message.chat_id, context), name=f"agy-console-{user.id}")


async def _agy_control_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    query = update.callback_query
    user = update.effective_user
    if query is None or user is None:
        return
    if not await _authorized(user.id):
        await query.answer("Not authorized", show_alert=True)
        return
    data = query.data or ""
    if not data.startswith("agy:key:"):
        await query.answer()
        return
    key = data.split(":", 2)[2]
    try:
        await _send_terminal_control(user.id, key)
        await query.answer("Enter sent")
    except RuntimeError:
        await query.answer("AGY console is not connected", show_alert=True)
    except Exception:
        logger.exception("Could not send AGY callback terminal control")
        await query.answer("Could not send key", show_alert=True)


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
    app.add_handler(CallbackQueryHandler(_agy_control_callback, pattern=r"^agy:key:"), group=-20)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _agy_text_handler), group=-20)
