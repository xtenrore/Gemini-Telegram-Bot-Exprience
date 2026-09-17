"""Resilient API-key pools for optional AI providers.

Keys are never logged.  The pool only exposes environment-variable labels in
status output so production diagnostics can identify which slot is healthy
without leaking credentials.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Iterable

from app.config import settings


@dataclass
class APIKeyState:
    label: str
    key: str
    calls: int = 0
    errors: int = 0
    cooldown_until: float = 0.0
    last_status: int | None = None

    @property
    def available(self) -> bool:
        return time.monotonic() >= self.cooldown_until


class RotatingAPIKeyPool:
    """Small in-memory key pool with rate-limit/auth failover and cooldowns."""

    def __init__(self, provider: str, keys: Iterable[tuple[str, str]]) -> None:
        self.provider = provider
        self._states: list[APIKeyState] = []
        seen: set[str] = set()
        for label, raw_key in keys:
            key = (raw_key or "").strip()
            if not key or key in seen:
                continue
            seen.add(key)
            self._states.append(APIKeyState(label=label, key=key))
        self._cursor = 0

    @property
    def configured(self) -> bool:
        return bool(self._states)

    def candidates(self) -> list[APIKeyState]:
        """Return currently available keys in round-robin order."""
        if not self._states:
            return []
        count = len(self._states)
        ordered = [self._states[(self._cursor + i) % count] for i in range(count)]
        return [state for state in ordered if state.available]

    def record_attempt(self, state: APIKeyState) -> None:
        state.calls += 1

    def mark_success(self, state: APIKeyState) -> None:
        state.last_status = 200
        state.cooldown_until = 0.0
        if self._states:
            self._cursor = (self._states.index(state) + 1) % len(self._states)

    def mark_http_failure(
        self,
        state: APIKeyState,
        status: int,
        *,
        retry_after: str | None = None,
    ) -> None:
        """Back off a credential only for failures that can be credential-specific."""
        state.errors += 1
        state.last_status = status
        now = time.monotonic()
        if status == 429:
            try:
                seconds = float(retry_after or "60")
            except (TypeError, ValueError):
                seconds = 60.0
            state.cooldown_until = now + min(max(seconds, 5.0), 300.0)
        elif status in (401, 403):
            # Invalid/disabled credential. Keep it out of the hot path for a while,
            # but allow recovery after a secret is rotated and the process lives on.
            state.cooldown_until = now + 900.0
        else:
            state.cooldown_until = max(state.cooldown_until, now + 10.0)
        if self._states:
            self._cursor = (self._states.index(state) + 1) % len(self._states)

    def report(self) -> list[dict[str, object]]:
        now = time.monotonic()
        return [
            {
                "label": state.label,
                "calls": state.calls,
                "errors": state.errors,
                "available": now >= state.cooldown_until,
                "cooldown_seconds": round(max(0.0, state.cooldown_until - now), 1),
                "last_status": state.last_status,
            }
            for state in self._states
        ]


def build_gemini_key_pool() -> RotatingAPIKeyPool:
    return RotatingAPIKeyPool(
        "gemini",
        (
            ("GEMINI_API_KEY", settings.gemini_api_key),
            ("GEMINI_API_KEY_2", settings.gemini_api_key_2),
        ),
    )


def build_groq_key_pool() -> RotatingAPIKeyPool:
    return RotatingAPIKeyPool(
        "groq",
        (
            ("GROQ_KEY", settings.groq_key),
            ("GROQ_KEY_2", settings.groq_key_2),
            # Backwards compatibility with the original Plane? variable name.
            ("GROQ_API_KEY", settings.groq_api_key),
        ),
    )
