"""Bound Mongo cursor batches for private Prediction Lab shadow scans.

Plane Alerts v4.2.1 keeps the existing shadow algorithms and scoring rules
unchanged. The only purpose of this layer is to prevent large route-history
queries from asking MongoDB to return too many point-heavy documents in one
network response.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from app import next_hour_shadow as next_hour_base
from app import next_hour_shadow_v43 as next_hour_quality
from app import sentinel_shadow as sentinel_base

# Route documents can contain up to hundreds of points. Small batches keep each
# Mongo response comfortably below the socket timeout without dropping records.
_BATCH_SIZES: dict[str, int] = {
    "flight_route_samples": 12,
    "prediction_sentinel_routes": 12,
    "prediction_lab_audit": 32,
    "users": 32,
    "locations": 32,
}


class _CollectionProxy:
    def __init__(self, collection: Any, batch_size: int) -> None:
        self._collection = collection
        self._batch_size = int(batch_size)

    def find(self, *args: Any, **kwargs: Any) -> Any:
        cursor = self._collection.find(*args, **kwargs)
        if self._batch_size > 0 and hasattr(cursor, "batch_size"):
            cursor.batch_size(self._batch_size)
        return cursor

    def __getattr__(self, name: str) -> Any:
        return getattr(self._collection, name)


class _DatabaseProxy:
    def __init__(self, database: Any) -> None:
        self._database = database

    def __getitem__(self, name: str) -> Any:
        collection = self._database[name]
        batch_size = _BATCH_SIZES.get(str(name))
        if batch_size is None:
            return collection
        return _CollectionProxy(collection, batch_size)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._database, name)


def _run_with_batched_db(
    module: Any,
    operation: Callable[[datetime | None], dict[str, int]],
    now: datetime | None,
) -> dict[str, int]:
    original_db = module._db
    database = original_db()
    if database is None:
        return operation(now)

    proxy = _DatabaseProxy(database)
    module._db = lambda: proxy
    try:
        return dict(operation(now))
    finally:
        module._db = original_db


def update_next_hour_shadow(now: datetime | None = None) -> dict[str, int]:
    """Run the existing v4.3 quality-gated Next60 shadow with bounded reads."""
    return _run_with_batched_db(next_hour_base, next_hour_quality.update_next_hour_shadow, now)


def update_sentinel_shadow(now: datetime | None = None) -> dict[str, int]:
    """Run the existing Europe sentinel shadow with bounded reads."""
    return _run_with_batched_db(sentinel_base, sentinel_base.update_sentinel_shadow, now)
