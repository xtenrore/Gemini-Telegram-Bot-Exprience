"""Bounded forensic decision recording for Plane Alerts v4.4.

The recorder is intentionally off the live decision path. Callers enqueue small
immutable snapshots; a single background writer batches them to MongoDB. Queue
pressure drops diagnostics rather than delaying the five-second alert loop.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from app.database import get_db

logger = logging.getLogger(__name__)

_RETENTION_DAYS = 14
_QUEUE_LIMIT = 512
_BATCH_SIZE = 32
_BATCH_WAIT_S = 0.20
_DEDUP_TTL_S = 120.0
_DEDUP_MAX = 4096
_STATE_MAX = 4096

_queue: asyncio.Queue[dict[str, Any]] | None = None
_writer_task: asyncio.Task[None] | None = None
_dedup: "OrderedDict[str, tuple[str, float]]" = OrderedDict()
_previous_state: "OrderedDict[str, str]" = OrderedDict()
_dropped = 0
_written = 0


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _safe(value: Any) -> Any:
    """Convert dataclasses/objects into bounded JSON-safe evidence."""
    if value is None or isinstance(value, (str, int, float, bool, datetime)):
        return value
    if isinstance(value, dict):
        return {str(k): _safe(v) for k, v in list(value.items())[:80]}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_safe(v) for v in list(value)[:40]]
    if hasattr(value, "__dict__"):
        return {str(k): _safe(v) for k, v in list(vars(value).items())[:80] if not str(k).startswith("_")}
    slots = getattr(type(value), "__slots__", ())
    if slots:
        return {str(k): _safe(getattr(value, k, None)) for k in list(slots)[:80]}
    return str(value)[:500]


def _signature(payload: dict[str, Any]) -> str:
    encoded = json.dumps(_safe(payload), sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:20]


def _prune(now_mono: float) -> None:
    while _dedup:
        _, (_, seen) = next(iter(_dedup.items()))
        if len(_dedup) <= _DEDUP_MAX and now_mono - seen <= _DEDUP_TTL_S:
            break
        _dedup.popitem(last=False)
    while len(_previous_state) > _STATE_MAX:
        _previous_state.popitem(last=False)


def _ensure_writer() -> asyncio.Queue[dict[str, Any]] | None:
    global _queue, _writer_task
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None
    if _queue is None:
        _queue = asyncio.Queue(maxsize=_QUEUE_LIMIT)
    if _writer_task is None or _writer_task.done():
        _writer_task = loop.create_task(_writer(), name="decision-recorder-writer")
    return _queue


async def _writer() -> None:
    global _written
    assert _queue is not None
    while True:
        first = await _queue.get()
        batch = [first]
        deadline = time.monotonic() + _BATCH_WAIT_S
        try:
            while len(batch) < _BATCH_SIZE:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    batch.append(await asyncio.wait_for(_queue.get(), timeout=remaining))
                except asyncio.TimeoutError:
                    break
            try:
                await get_db()["decision_records"].insert_many(batch, ordered=False)
                _written += len(batch)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("decision_recorder_batch_write_failed count=%d", len(batch))
        finally:
            for _ in batch:
                _queue.task_done()


def recorder_stats() -> dict[str, int]:
    return {
        "queued": _queue.qsize() if _queue is not None else 0,
        "written": _written,
        "dropped": _dropped,
        "queue_limit": _QUEUE_LIMIT,
    }


def record_decision(
    *,
    subsystem: str,
    event: str,
    state_key: str,
    state: str,
    evidence: dict[str, Any],
    reason_code: str = "",
    decision: str = "",
    user_id: int | None = None,
    notification_id: str = "",
    dedup_signature: dict[str, Any] | None = None,
    force: bool = False,
) -> str:
    """Queue one forensic snapshot and return its stable decision id.

    State-identical route evaluations are sampled at most once per dedup window;
    transitions, deliveries and explicit feedback can pass ``force=True``.
    """
    global _dropped
    now_mono = time.monotonic()
    _prune(now_mono)
    sig_payload = dedup_signature if dedup_signature is not None else {
        "state": state,
        "reason_code": reason_code,
        "decision": decision,
    }
    sig = _signature(sig_payload)
    previous = _dedup.get(state_key)
    if not force and previous and previous[0] == sig and now_mono - previous[1] <= _DEDUP_TTL_S:
        return ""
    _dedup[state_key] = (sig, now_mono)
    _dedup.move_to_end(state_key)

    decision_id = "dec-" + uuid.uuid4().hex[:20]
    previous_state = _previous_state.get(state_key, "")
    _previous_state[state_key] = state
    _previous_state.move_to_end(state_key)

    now = _utcnow()
    payload: dict[str, Any] = {
        "decision_id": decision_id,
        "timestamp": now,
        "expires_at": now + timedelta(days=_RETENTION_DAYS),
        "subsystem": str(subsystem)[:64],
        "event": str(event)[:64],
        "decision": str(decision)[:64],
        "decision_reason_code": str(reason_code)[:120],
        "previous_state": str(previous_state)[:120],
        "new_state": str(state)[:120],
        "evidence": _safe(evidence),
    }
    if user_id is not None:
        payload["user_id"] = int(user_id)
    if notification_id:
        payload["notification_id"] = str(notification_id)[:128]

    queue = _ensure_writer()
    if queue is None:
        _dropped += 1
        return ""
    try:
        queue.put_nowait(payload)
    except asyncio.QueueFull:
        _dropped += 1
        if _dropped == 1 or _dropped % 100 == 0:
            logger.warning("decision_recorder_queue_full dropped=%d limit=%d", _dropped, _QUEUE_LIMIT)
        return ""
    return decision_id


def trajectory_samples(samples: Iterable[Any], *, limit: int = 12) -> list[dict[str, Any]]:
    ordered = list(samples)[-max(1, min(int(limit), 20)):]
    result: list[dict[str, Any]] = []
    for sample in ordered:
        result.append({
            "timestamp": getattr(sample, "timestamp", None),
            "latitude": getattr(sample, "latitude", None),
            "longitude": getattr(sample, "longitude", None),
            "altitude_m": getattr(sample, "altitude_m", None),
            "speed_kts": getattr(sample, "speed_kts", None),
            "track_deg": getattr(sample, "heading_deg", None),
            "vertical_rate_mps": getattr(sample, "vertical_rate_mps", None),
            "position_age_s": getattr(sample, "position_age_s", None),
        })
    return result


def reset_recorder_for_tests() -> None:
    global _queue, _writer_task, _dropped, _written
    if _writer_task is not None and not _writer_task.done():
        _writer_task.cancel()
    _queue = None
    _writer_task = None
    _dedup.clear()
    _previous_state.clear()
    _dropped = 0
    _written = 0
