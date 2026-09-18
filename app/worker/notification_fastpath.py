"""Keep persistence out of the time-critical Telegram alert path.

Plane alerts are only useful if the user receives them before closest approach.
The notification module historically awaited MongoDB snapshot/history writes
before calling Telegram. A slow database operation could therefore turn a good
prediction into a late notification.

This production guard preserves the same persistence calls but schedules them as
bounded background work. Telegram delivery is never made to wait on those
telemetry writes. Immediate in-process lifecycle/cooldown state remains owned by
the monitor; these records are durable history/feedback data rather than the
physical pass decision itself.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from typing import Any

from app.worker import notifications as notification_mod

logger = logging.getLogger(__name__)

_MAX_BACKGROUND_WRITES = 96
_WRITE_CONCURRENCY = 8
_INSTALLED = False
_tasks: set[asyncio.Task[Any]] = set()
_write_semaphore: asyncio.Semaphore | None = None

_ORIGINAL_RECORD_PHOTO_SNAPSHOT = notification_mod._record_photo_snapshot
_ORIGINAL_GET_DB = notification_mod.get_db


def _semaphore() -> asyncio.Semaphore:
    global _write_semaphore
    if _write_semaphore is None:
        _write_semaphore = asyncio.Semaphore(_WRITE_CONCURRENCY)
    return _write_semaphore


async def _run_background_write(
    label: str,
    operation_factory: Callable[[], Coroutine[Any, Any, Any]],
) -> None:
    try:
        async with _semaphore():
            await operation_factory()
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("notification_background_write_failed kind=%s", label)


def _spawn_background_write(
    label: str,
    operation_factory: Callable[[], Coroutine[Any, Any, Any]],
) -> bool:
    # Persistence is valuable, but it is not allowed to delay a live spotting
    # alert. A hard cap prevents a database outage from creating unbounded RAM
    # growth. The immediate alert lifecycle is already stored in process memory.
    if len(_tasks) >= _MAX_BACKGROUND_WRITES:
        logger.error(
            "notification_background_write_dropped kind=%s active=%d limit=%d",
            label,
            len(_tasks),
            _MAX_BACKGROUND_WRITES,
        )
        return False

    task = asyncio.create_task(
        _run_background_write(label, operation_factory),
        name=f"notification-persist:{label}",
    )
    _tasks.add(task)

    def _finished(done: asyncio.Task[Any]) -> None:
        _tasks.discard(done)
        try:
            done.result()
        except asyncio.CancelledError:
            pass
        except Exception:
            # _run_background_write already logs operational failures. This is
            # only a final safety net for unexpected task-wrapper failures.
            logger.exception("notification_background_task_failed kind=%s", label)

    task.add_done_callback(_finished)
    return True


async def _record_photo_snapshot_nonblocking(*args: Any, **kwargs: Any) -> None:
    _spawn_background_write(
        "photo_snapshot",
        lambda: _ORIGINAL_RECORD_PHOTO_SNAPSHOT(*args, **kwargs),
    )


class _NotificationHistoryCollectionProxy:
    def __init__(self, collection: Any) -> None:
        self._collection = collection

    async def update_one(self, *args: Any, **kwargs: Any) -> None:
        _spawn_background_write(
            "notification_history",
            lambda: self._collection.update_one(*args, **kwargs),
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._collection, name)


class _NotificationDatabaseProxy:
    def __init__(self, database: Any) -> None:
        self._database = database

    def __getitem__(self, name: str) -> Any:
        collection = self._database[name]
        if name == "notification_history":
            return _NotificationHistoryCollectionProxy(collection)
        return collection

    def __getattr__(self, name: str) -> Any:
        return getattr(self._database, name)


def _get_db_nonblocking_history() -> Any:
    return _NotificationDatabaseProxy(_ORIGINAL_GET_DB())


def background_write_count() -> int:
    """Expose current persistence backlog for diagnostics/tests."""
    return len(_tasks)


def install_notification_fastpath() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    notification_mod._record_photo_snapshot = _record_photo_snapshot_nonblocking
    notification_mod.get_db = _get_db_nonblocking_history
    _INSTALLED = True
    logger.info(
        "Notification fastpath enabled: Telegram delivery no longer awaits Mongo snapshot/history writes; concurrency=%d backlog_limit=%d",
        _WRITE_CONCURRENCY,
        _MAX_BACKGROUND_WRITES,
    )
