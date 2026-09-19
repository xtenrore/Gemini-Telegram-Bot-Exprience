"""Single-flight background execution for non-critical AGY shadow audits.

The Prediction Lab handoff loop must stay responsive even when MongoDB is slow.
Next-hour and Europe-sentinel audits are shadow-only, so at most one of them is
allowed to perform blocking database work at a time. A slow/failing audit never
blocks context refresh or ChatGPT handoff publication in the bridge thread.
"""
from __future__ import annotations

from concurrent.futures import Executor, Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable, Mapping


@dataclass(frozen=True)
class ShadowAuditEvent:
    kind: str
    ok: bool
    counters: dict[str, Any]
    error_type: str = ""
    error_message: str = ""


class SingleFlightShadowAudits:
    """Run at most one blocking shadow audit outside the bridge main loop."""

    def __init__(
        self,
        *,
        next_hour_fn: Callable[[], Mapping[str, Any]],
        sentinel_fn: Callable[[], Mapping[str, Any]],
        next_hour_interval_s: float = 60.0,
        sentinel_interval_s: float = 120.0,
        executor: Executor | None = None,
    ) -> None:
        self._next_hour_fn = next_hour_fn
        self._sentinel_fn = sentinel_fn
        self._next_hour_interval_s = max(1.0, float(next_hour_interval_s))
        self._sentinel_interval_s = max(1.0, float(sentinel_interval_s))
        self._executor = executor or ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="agy-shadow-audit",
        )
        self._owns_executor = executor is None
        self._future: Future | None = None
        self._kind = ""
        self.next_hour_due = 0.0
        self.sentinel_due = 0.0

    @property
    def busy(self) -> bool:
        return self._future is not None and not self._future.done()

    @property
    def active_kind(self) -> str:
        return self._kind

    def _consume_completed(self) -> list[ShadowAuditEvent]:
        future = self._future
        if future is None or not future.done():
            return []

        kind = self._kind
        self._future = None
        self._kind = ""
        try:
            counters = dict(future.result() or {})
            return [ShadowAuditEvent(kind=kind, ok=True, counters=counters)]
        except Exception as exc:  # shadow work must never escape into bridge loop
            message = str(exc).replace("\n", " ").strip()
            if len(message) > 180:
                message = message[:177] + "..."
            return [
                ShadowAuditEvent(
                    kind=kind,
                    ok=False,
                    counters={},
                    error_type=type(exc).__name__,
                    error_message=message,
                )
            ]

    def _schedule_due(self, now: float) -> None:
        if self._future is not None:
            return

        due: list[tuple[float, str, Callable[[], Mapping[str, Any]]]] = []
        if now >= self.next_hour_due:
            due.append((self.next_hour_due, "next_hour", self._next_hour_fn))
        if now >= self.sentinel_due:
            due.append((self.sentinel_due, "sentinel", self._sentinel_fn))
        if not due:
            return

        # Oldest due job wins so a long next-hour audit cannot starve sentinel.
        _, kind, function = min(due, key=lambda item: item[0])
        self._kind = kind
        self._future = self._executor.submit(function)
        if kind == "next_hour":
            self.next_hour_due = now + self._next_hour_interval_s
        else:
            self.sentinel_due = now + self._sentinel_interval_s

    def poll(self, now: float) -> list[ShadowAuditEvent]:
        """Collect a finished job and schedule at most one due job; never wait."""
        events = self._consume_completed()
        self._schedule_due(float(now))
        return events

    def shutdown(self) -> None:
        if self._owns_executor:
            self._executor.shutdown(wait=False, cancel_futures=True)
