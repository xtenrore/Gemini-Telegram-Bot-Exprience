"""OpenSky API key rotation manager.

Loads multiple OpenSky credentials from JSON files in the configured
``api_keys_dir`` directory and rotates through them as credits are consumed
or rate limits are hit.

Expected JSON format per file::

    {"username": "myuser", "password": "mypass"}
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)

# OpenSky resets credits daily at midnight UTC.
# Registered accounts get ~4000 credits/day.
_DAILY_CREDIT_LIMIT = 4000


@dataclass
class OpenSkyKey:
    """Represents a single OpenSky API credential."""

    username: str
    password: str
    source_file: str  # filename it was loaded from

    # Runtime tracking
    requests_made: int = 0
    rate_limit_hits: int = 0
    last_rate_limited_at: float = 0.0
    is_exhausted: bool = False  # True when credits likely used up for the day
    last_reset_day: int = 0  # day-of-year when counters were last reset


@dataclass
class KeyManagerStatus:
    """Snapshot of the key manager state for the admin dashboard."""

    total_keys: int = 0
    active_key_index: int = 0
    keys: list[dict] = field(default_factory=list)
    all_exhausted: bool = False


class OpenSkyKeyManager:
    """Manages rotation through multiple OpenSky API credentials.

    Usage::

        manager = OpenSkyKeyManager()
        manager.load_keys()           # Call once at startup
        creds = manager.get_current_credentials()
        if creds:
            username, password = creds
        manager.record_request()      # After a successful request
        manager.mark_rate_limited()   # If HTTP 429 received
    """

    def __init__(self) -> None:
        self._keys: list[OpenSkyKey] = []
        self._current_index: int = 0

    # ── Loading ──────────────────────────────────────────────────────────

    def load_keys(self) -> int:
        """Load API keys from JSON files in ``settings.api_keys_dir``.

        Returns the number of keys loaded.
        """
        keys_dir = Path(settings.api_keys_dir)
        if not keys_dir.exists():
            logger.warning(
                "API keys directory '%s' does not exist — OpenSky disabled.",
                keys_dir,
            )
            return 0

        json_files = sorted(keys_dir.glob("*.json"))
        if not json_files:
            logger.warning(
                "No .json key files found in '%s' — OpenSky disabled.", keys_dir
            )
            return 0

        self._keys = []
        for fpath in json_files:
            try:
                data = json.loads(fpath.read_text(encoding="utf-8"))
                username = data.get("username", "").strip()
                password = data.get("password", "").strip()
                if username and password:
                    self._keys.append(
                        OpenSkyKey(
                            username=username,
                            password=password,
                            source_file=fpath.name,
                        )
                    )
                    logger.info("Loaded OpenSky key from %s (user: %s)", fpath.name, username)
                else:
                    logger.warning("Skipping %s — missing username or password.", fpath.name)
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Failed to read key file %s: %s", fpath.name, exc)

        self._current_index = 0
        logger.info("OpenSky key manager: %d key(s) loaded.", len(self._keys))
        return len(self._keys)

    # ── Credential access ────────────────────────────────────────────────

    def get_current_credentials(self) -> tuple[str, str] | None:
        """Return ``(username, password)`` for the active key, or ``None``.

        Returns ``None`` if no keys are loaded or ALL keys are exhausted.
        """
        if not self._keys:
            return None

        self._maybe_reset_daily_counters()

        # Find a non-exhausted key starting from current index
        checked = 0
        while checked < len(self._keys):
            key = self._keys[self._current_index]
            if not key.is_exhausted:
                return (key.username, key.password)
            self._current_index = (self._current_index + 1) % len(self._keys)
            checked += 1

        logger.warning("All %d OpenSky keys are exhausted for today.", len(self._keys))
        return None

    @property
    def has_keys(self) -> bool:
        """Whether any keys are loaded at all."""
        return len(self._keys) > 0

    @property
    def all_exhausted(self) -> bool:
        """Whether every loaded key is exhausted for the day."""
        if not self._keys:
            return True
        return all(k.is_exhausted for k in self._keys)

    # ── Tracking ─────────────────────────────────────────────────────────

    def record_request(self) -> None:
        """Record a successful request against the current key."""
        if not self._keys:
            return
        key = self._keys[self._current_index]
        key.requests_made += 1

        # Proactively mark as exhausted when approaching limit
        if key.requests_made >= _DAILY_CREDIT_LIMIT - 50:
            logger.warning(
                "OpenSky key '%s' approaching credit limit (%d/%d) — rotating.",
                key.username,
                key.requests_made,
                _DAILY_CREDIT_LIMIT,
            )
            key.is_exhausted = True
            self._rotate_to_next()

    def mark_rate_limited(self) -> None:
        """Mark the current key as rate-limited and rotate to the next one."""
        if not self._keys:
            return
        key = self._keys[self._current_index]
        key.rate_limit_hits += 1
        key.last_rate_limited_at = time.time()
        key.is_exhausted = True
        logger.warning(
            "OpenSky key '%s' rate-limited (hit #%d) — rotating to next key.",
            key.username,
            key.rate_limit_hits,
        )
        self._rotate_to_next()

    def _rotate_to_next(self) -> None:
        """Move to the next available key."""
        original = self._current_index
        self._current_index = (self._current_index + 1) % len(self._keys)
        # Prevent infinite loop if all exhausted
        attempts = 0
        while self._keys[self._current_index].is_exhausted and attempts < len(self._keys):
            self._current_index = (self._current_index + 1) % len(self._keys)
            attempts += 1

        if self._current_index != original and not self._keys[self._current_index].is_exhausted:
            logger.info(
                "Rotated to OpenSky key '%s' (index %d).",
                self._keys[self._current_index].username,
                self._current_index,
            )

    # ── Daily reset ──────────────────────────────────────────────────────

    def _maybe_reset_daily_counters(self) -> None:
        """Reset all counters if we've crossed into a new UTC day."""
        import datetime

        today = datetime.datetime.now(datetime.timezone.utc).timetuple().tm_yday
        for key in self._keys:
            if key.last_reset_day != today:
                key.requests_made = 0
                key.rate_limit_hits = 0
                key.is_exhausted = False
                key.last_reset_day = today
                logger.info("Daily reset for OpenSky key '%s'.", key.username)

    # ── Status for admin dashboard ───────────────────────────────────────

    def get_status(self) -> KeyManagerStatus:
        """Return a snapshot of all key states for the admin panel."""
        keys_info = []
        for i, key in enumerate(self._keys):
            keys_info.append({
                "index": i,
                "username": key.username,
                "source_file": key.source_file,
                "requests_made": key.requests_made,
                "estimated_remaining": max(0, _DAILY_CREDIT_LIMIT - key.requests_made),
                "rate_limit_hits": key.rate_limit_hits,
                "is_exhausted": key.is_exhausted,
                "is_active": i == self._current_index,
            })

        return KeyManagerStatus(
            total_keys=len(self._keys),
            active_key_index=self._current_index,
            keys=keys_info,
            all_exhausted=self.all_exhausted,
        )


# ── Module-level singleton ──────────────────────────────────────────────────
opensky_key_manager = OpenSkyKeyManager()
