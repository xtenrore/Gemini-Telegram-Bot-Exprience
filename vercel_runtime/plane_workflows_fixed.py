"""Safe workflow overrides for Plane? v3.2.

Vercel's Python workflow body runs in a deterministic sandbox. Starting another
workflow directly from that body performs network I/O and is rejected. Put the
child-workflow launch in a durable step, where side effects are allowed.
"""
from __future__ import annotations

from typing import Any

from vercel.workflow import start

from plane_workflows import (
    TelegramUpdate,
    _should_detach_telegram_update,
    configure_telegram,
    monitor_workflow,
    process_telegram_update,
    telegram_slow_update_workflow,
    webhook_path_secret,
    telegram_secret_header,
    wf,
)


@wf.step
async def launch_slow_telegram_update(
    config: dict[str, Any],
    generation: str,
    payload: dict[str, Any],
) -> bool:
    """Launch expensive photo/conditions work outside the deterministic workflow body."""
    await start(telegram_slow_update_workflow, config, generation, payload)
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
        await process_telegram_update(
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
