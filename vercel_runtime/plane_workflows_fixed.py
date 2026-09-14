"""Fast workflow overrides for Plane? v3.3.

Telegram callback queries are acknowledged by the webhook response before the durable
handler finishes. This module suppresses only the later duplicate *empty*
``CallbackQuery.answer()`` call inside the legacy handlers while preserving alerts,
toasts and URLs that intentionally carry user-visible content.

Vercel workflow bodies run in a deterministic sandbox, so child-workflow launches stay
inside durable steps where side effects are allowed.
"""
from __future__ import annotations

import time
from typing import Any

from vercel.workflow import start

from plane_workflows import (
    TelegramUpdate,
    _ensure_telegram_runtime,
    _should_detach_telegram_update,
    configure_telegram,
    logger,
    monitor_workflow,
    telegram_secret_header,
    webhook_path_secret,
    wf,
)

_callback_answer_patched = False


def _install_preacked_callback_answer() -> None:
    """Skip the duplicate silent callback ACK after ingress already acknowledged it."""
    global _callback_answer_patched
    if _callback_answer_patched:
        return

    from telegram import CallbackQuery

    original_answer = CallbackQuery.answer

    async def _answer(self: Any, *args: Any, **kwargs: Any) -> Any:
        # cb_handler's first ``await query.answer()`` is now handled in the webhook
        # response. Do not make a second Bot API call for that empty ACK. Explicit
        # alerts/toasts/URLs must still reach Telegram normally.
        text = kwargs.get("text")
        show_alert = bool(kwargs.get("show_alert", False))
        url = kwargs.get("url")
        if not args and not text and not show_alert and not url:
            return True
        return await original_answer(self, *args, **kwargs)

    CallbackQuery.answer = _answer
    _callback_answer_patched = True


@wf.step
async def process_telegram_update_v33(
    config: dict[str, Any],
    generation: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Process one Telegram update with v3.3 callback latency instrumentation."""
    started = time.perf_counter()
    update_id = payload.get("update_id")
    callback = isinstance(payload.get("callback_query"), dict)
    try:
        runtime_started = time.perf_counter()
        application = await _ensure_telegram_runtime(config, generation)
        runtime_ms = int((time.perf_counter() - runtime_started) * 1000)

        from telegram import Update

        _install_preacked_callback_answer()
        parse_started = time.perf_counter()
        update = Update.de_json(payload, application.bot)
        parse_ms = int((time.perf_counter() - parse_started) * 1000)

        handler_started = time.perf_counter()
        if update is not None:
            await application.process_update(update)
        handler_ms = int((time.perf_counter() - handler_started) * 1000)
        total_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "Telegram v3.3 update processed update_id=%s callback=%s runtime_ms=%d parse_ms=%d handler_ms=%d total_ms=%d",
            update_id,
            callback,
            runtime_ms,
            parse_ms,
            handler_ms,
            total_ms,
        )
        return {
            "processed": True,
            "update_id": update_id,
            "callback": callback,
            "runtime_ms": runtime_ms,
            "handler_ms": handler_ms,
            "elapsed_ms": total_ms,
        }
    except Exception as exc:
        logger.exception(
            "Telegram v3.3 update processing failed update_id=%s callback=%s type=%s",
            update_id,
            callback,
            type(exc).__name__,
        )
        return {"processed": False, "update_id": update_id, "callback": callback}


@wf.workflow
async def telegram_slow_update_workflow_v33(
    config: dict[str, Any],
    generation: str,
    payload: dict[str, Any],
) -> None:
    """Process expensive photo/conditions work without blocking ordinary input."""
    await process_telegram_update_v33(config=config, generation=generation, payload=payload)


@wf.step
async def launch_slow_telegram_update(
    config: dict[str, Any],
    generation: str,
    payload: dict[str, Any],
) -> bool:
    """Launch expensive work outside the deterministic workflow body."""
    await start(telegram_slow_update_workflow_v33, config, generation, payload)
    return True


@wf.workflow
async def telegram_workflow(
    config: dict[str, Any],
    generation: str,
    base_url: str,
) -> None:
    await configure_telegram(config=config, generation=generation, base_url=base_url)
    path_secret = webhook_path_secret(str(config["telegram_bot_token"]), generation)
    hook_token = f"telegram:{path_secret}"

    async for event in TelegramUpdate.wait(token=hook_token):
        if _should_detach_telegram_update(event.update):
            await launch_slow_telegram_update(
                config=config,
                generation=generation,
                payload=event.update,
            )
            continue
        await process_telegram_update_v33(
            config=config,
            generation=generation,
            payload=event.update,
        )


__all__ = [
    "TelegramUpdate",
    "monitor_workflow",
    "telegram_secret_header",
    "telegram_workflow",
    "webhook_path_secret",
    "wf",
]
