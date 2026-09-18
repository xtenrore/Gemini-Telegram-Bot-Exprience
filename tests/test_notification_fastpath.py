from __future__ import annotations

import asyncio

import pytest

from app.worker import notification_fastpath as fastpath


@pytest.mark.asyncio
async def test_photo_snapshot_persistence_does_not_block_alert_path(monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_snapshot(*args, **kwargs):
        started.set()
        await release.wait()

    monkeypatch.setattr(fastpath, "_ORIGINAL_RECORD_PHOTO_SNAPSHOT", slow_snapshot)

    # The patched await used by notifications.py must return before the actual
    # Mongo write finishes.
    await asyncio.wait_for(
        fastpath._record_photo_snapshot_nonblocking(1, object(), 5.0, "n1", 30.0),
        timeout=0.05,
    )
    await asyncio.wait_for(started.wait(), timeout=0.05)
    assert fastpath.background_write_count() >= 1

    release.set()
    await asyncio.sleep(0)
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_notification_history_update_is_scheduled_not_awaited():
    started = asyncio.Event()
    release = asyncio.Event()

    class SlowCollection:
        async def update_one(self, *args, **kwargs):
            started.set()
            await release.wait()

    proxy = fastpath._NotificationHistoryCollectionProxy(SlowCollection())
    await asyncio.wait_for(proxy.update_one({"_id": "n1"}, {"$set": {"x": 1}}), timeout=0.05)
    await asyncio.wait_for(started.wait(), timeout=0.05)
    assert fastpath.background_write_count() >= 1

    release.set()
    await asyncio.sleep(0)
    await asyncio.sleep(0)


def test_notification_database_proxy_only_intercepts_history():
    history = object()
    snapshots = object()

    class FakeDb:
        def __getitem__(self, name):
            return {"notification_history": history, "photo_alert_snapshots": snapshots}[name]

    proxy = fastpath._NotificationDatabaseProxy(FakeDb())
    assert isinstance(proxy["notification_history"], fastpath._NotificationHistoryCollectionProxy)
    assert proxy["photo_alert_snapshots"] is snapshots


@pytest.mark.asyncio
async def test_background_queue_is_hard_bounded(monkeypatch):
    release = asyncio.Event()
    local_tasks = set()
    monkeypatch.setattr(fastpath, "_tasks", local_tasks)
    monkeypatch.setattr(fastpath, "_MAX_BACKGROUND_WRITES", 2)

    async def blocked():
        await release.wait()

    assert fastpath._spawn_background_write("one", blocked)
    assert fastpath._spawn_background_write("two", blocked)
    assert fastpath._spawn_background_write("three", blocked) is False
    assert len(local_tasks) == 2

    release.set()
    await asyncio.gather(*list(local_tasks), return_exceptions=True)
    await asyncio.sleep(0)
