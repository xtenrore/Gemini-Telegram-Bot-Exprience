"""Telegram bridge for the private Plane Alerts Antigravity worker."""
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

TERMINAL_CONTROLS: dict[str, str] = {
    "enter": "\r",
    "up": "\x1b[A",
    "down": "\x1b[B",
    "left": "\x1b[D",
    "right": "\x1b[C",
    "space": " ",
    "esc": "\x1b",
    "ctrlc": "\x03",
}
CONTROL_LABELS = {
    "enter": "↵ Enter",
    "up": "↑",
    "down": "↓",
    "left": "←",
    "right": "→",
    "space": "␠ Space",
    "esc": "Esc",
    "ctrlc": "Ctrl+C",
}

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
    rows.extend(
        [
            [
                InlineKeyboardButton("↑", callback_data="agy:key:up"),
                InlineKeyboardButton("↵ Enter", callback_data="agy:key:enter"),
                InlineKeyboardButton("↓", callback_data="agy:key:down"),
            ],
            [
                InlineKeyboardButton("←", callback_data="agy:key:left"),
                InlineKeyboardButton("␠ Space", callback_data="agy:key:space"),
                InlineKeyboardButton("→", callback_data="agy:key:right"),
            ],
            [
                InlineKeyboardButton("Esc", callback_data="agy:key:esc"),
                InlineKeyboardButton("Ctrl+C", callback_data="agy:key:ctrlc"),
            ],
        ]
    )
    return InlineKeyboardMarkup(rows)


def _sanitize_google_url(candidate: str) -> str | None:
    value = candidate.strip()
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
    candidates = list(lines)
    if len(lines) > 1:
        candidates.append("".join(lines))
    for text in candidates:
        for match in _URL_RE.findall(text):
            cleaned = _sanitize_google_url(match)
            if cleaned and cleaned not in found:
                found.append(cleaned)
    return found


def _normalize_authorization_code(raw: str) -> str:
    compact = re.sub(r"\s+", "", raw or "")
    if not compact:
        raise ValueError("Authorization code is empty")
    if compact.startswith("4/") and len(compact) % 2 == 0:
        half = len(compact) // 2
        if compact[:half] == compact[half:]:
            compact = compact[:half]
    return compact


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


async def _send_console_lines(session: _Session, chat_id: int, bot: Any, lines: list[str]) -> None:
    urls = _extract_google_urls(lines)
    for oauth_url in urls:
        if oauth_url in session.seen_urls:
            continue
        session.seen_urls.add(oauth_url)
        await bot.send_message(
            chat_id=chat_id,
            text=(
                "🔐 Google sign-in\n\n"
                "Tap the button below. After Google gives you an authorization code, send:\n\n"
                "/agy code YOUR_CODE"
            ),
            reply_markup=_controls_keyboard(oauth_url),
        )
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
    code = _normalize_authorization_code(code)
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
            await message.reply_text(
                "Authorization code sanitized and sent to Antigravity. If the TUI still waits, tap ↵ Enter once.",
                reply_markup=_controls_keyboard(),
            )
        except RuntimeError:
            await message.reply_text("AGY console is not connected. Send /agy first.")
        except Exception as exc:
            logger.exception("Could not send AGY authorization code")
            await message.reply_text(f"Could not send authorization code ({type(exc).__name__}).")
        return

    if action in TERMINAL_CONTROLS:
        try:
            await _send_terminal_control(user.id, action)
            await message.reply_text(
                f"{CONTROL_LABELS.get(action, action)} sent to Antigravity.",
                reply_markup=_controls_keyboard(),
            )
        except RuntimeError:
            await message.reply_text("AGY console is not connected. Send /agy first.")
        except Exception as exc:
            logger.exception("Could not send AGY terminal control")
            await message.reply_text(f"Could not send {action} ({type(exc).__name__}).")
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
            await message.reply_text(
                "AGY Prediction Lab\n"
                f"Supervisor: {worker.get('status', 'unknown')}\n"
                f"Goal loop enabled: {worker.get('enabled', False)}\n"
                f"Goal running: {worker.get('goal_running', False)}\n"
                f"Next run: {worker.get('next_run_iso') or 'not scheduled'}\n"
                "Paid credit overages: OFF\n"
                "Model: Gemini 3.1 Pro High"
            )
        except Exception as exc:
            logger.exception("AGY status failed")
            await message.reply_text(f"Could not read AGY status ({type(exc).__name__}).")
        return

    if action == "goal":
        await message.reply_text("The autonomous goal loop stays locked until AGY Google login is confirmed.")
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
                    + " Use the controls below or /agy stop to disconnect.",
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
        "Use ↑/↓/←/→, Space, Enter, Esc, or Ctrl+C with the buttons below. "
        "For Google auth codes use /agy code YOUR_CODE.",
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
        await query.answer(f"{CONTROL_LABELS.get(key, key)} sent")
    except RuntimeError:
        await query.answer("AGY console is not connected", show_alert=True)
    except Exception:
        logger.exception("Could not send AGY callback terminal control")
        await query.answer("Could not send key", show_alert=True)


async def handle_agy_text_if_active(update: Update) -> bool:
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
        raise ApplicationHandlerStop


def register_agy_console_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("agy", cmd_agy), group=-20)
    app.add_handler(CallbackQueryHandler(_agy_control_callback, pattern=r"^agy:key:"), group=-20)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _agy_text_handler), group=-20)
