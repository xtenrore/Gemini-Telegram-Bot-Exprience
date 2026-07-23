"""OpenSky API key rotation manager with OAuth2 token support.

Loads multiple OpenSky credentials from JSON files in the configured
``api_keys_dir`` directory and rotates through them as credits are consumed
or rate limits are hit.

Expected JSON format per file (OAuth2 — current)::

    {"clientId": "my-client-id", "clientSecret": "my-secret"}

Legacy format also supported::

    {"username": "myuser", "password": "mypass"}
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# OpenSky resets credits daily at midnight UTC.
# Registered accounts get ~4000 credits/day.
_DAILY_CREDIT_LIMIT = 4000


@dataclass
class OpenSkyKey:
    """Represents a single OpenSky API credential."""

    client_id: str
    client_secret: str
    source_file: str  # filename it was loaded from

    # OAuth2 token cache
    access_token: str = ""
    token_expires_at: float = 0.0  # monotonic time when token expires

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
        manager.load_keys()             # Call once at startup
        token = await manager.get_bearer_token()
        if token:
            headers = {"Authorization": f"Bearer {token}"}
        manager.record_request()        # After a successful request
        manager.mark_rate_limited()     # If HTTP 429 received
    """

    def __init__(self) -> None:
        self._keys: list[OpenSkyKey] = []
        self._current_index: int = 0
        self._token_lock = asyncio.Lock()

    # ── Loading ──────────────────────────────────────────────────────────

    def load_keys(self) -> int:
        """Load API keys from JSON files in ``settings.api_keys_dir``.

        Supports both OAuth2 (clientId/clientSecret) and legacy
        (username/password) credential formats.

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

                # Try OAuth2 format first (clientId / clientSecret)
                client_id = (data.get("clientId") or "").strip()
                client_secret = (data.get("clientSecret") or "").strip()

                # Fallback to legacy format (username / password)
                if not client_id:
                    client_id = (data.get("username") or "").strip()
                if not client_secret:
                    client_secret = (data.get("password") or "").strip()

                if client_id and client_secret:
                    self._keys.append(
                        OpenSkyKey(
                            client_id=client_id,
                            client_secret=client_secret,
                            source_file=fpath.name,
                        )
                    )
                    logger.info(
                        "Loaded OpenSky key from %s (client: %s)",
                        fpath.name,
                        client_id[:20] + "..." if len(client_id) > 20 else client_id,
                    )
                else:
                    logger.warning(
                        "Skipping %s — missing clientId/clientSecret.", fpath.name
                    )
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Failed to read key file %s: %s", fpath.name, exc)

        self._current_index = 0
        logger.info("OpenSky key manager: %d key(s) loaded.", len(self._keys))
        return len(self._keys)

    # ── Token acquisition ────────────────────────────────────────────────

    async def get_bearer_token(self) -> str | None:
        """Return a valid Bearer token for the active key, or ``None``.

        Acquires a new token if the cached one has expired. Tokens are
        cached per-key with ~25 minute effective TTL (actual is 30 min,
        we refresh early).
        """
        if not self._keys:
            return None

        self._maybe_reset_daily_counters()

        # Find a non-exhausted key
        key = self._find_available_key()
        if key is None:
            return None

        # Check if cached token is still valid (refresh 5 min early)
        if key.access_token and time.monotonic() < key.token_expires_at - 300:
            return key.access_token

        # Need to acquire a new token
        async with self._token_lock:
            # Double-check after acquiring lock
            if key.access_token and time.monotonic() < key.token_expires_at - 300:
                return key.access_token

            token = await self._acquire_token(key)
            return token

    async def _acquire_token(self, key: OpenSkyKey) -> str | None:
        """Exchange client credentials for an OAuth2 Bearer token."""
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
                resp = await client.post(
                    settings.opensky_token_url,
                    data={
                        "grant_type": "client_credentials",
                        "client_id": key.client_id,
                        "client_secret": key.client_secret,
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                resp.raise_for_status()
                data = resp.json()

            access_token = data.get("access_token", "")
            expires_in = data.get("expires_in", 1800)  # default 30 min

            if not access_token:
                logger.warning(
                    "OpenSky token response missing access_token for %s",
                    key.client_id[:20],
                )
                return None

            key.access_token = access_token
            key.token_expires_at = time.monotonic() + expires_in

            logger.info(
                "Acquired OAuth2 token for %s (expires in %ds)",
                key.client_id[:20] + "...",
                expires_in,
            )
            return access_token

        except Exception as exc:
            logger.warning(
                "Failed to acquire OAuth2 token for %s: %s",
                key.client_id[:20],
                exc,
            )
            return None

    async def refresh_current_token(self) -> str | None:
        """Force-refresh the token for the current key (e.g. after 401)."""
        key = self._find_available_key()
        if key is None:
            return None

        # Invalidate cached token
        key.access_token = ""
        key.token_expires_at = 0.0

        async with self._token_lock:
            return await self._acquire_token(key)

    # ── Credential access (legacy compat) ────────────────────────────────

    def get_current_credentials(self) -> tuple[str, str] | None:
        """Return ``(client_id, client_secret)`` for the active key, or ``None``.

        Returns ``None`` if no keys are loaded or ALL keys are exhausted.
        """
        key = self._find_available_key()
        if key is None:
            return None
        return (key.client_id, key.client_secret)

    def _find_available_key(self) -> OpenSkyKey | None:
        """Find a non-exhausted key starting from current index."""
        if not self._keys:
            return None

        checked = 0
        while checked < len(self._keys):
            key = self._keys[self._current_index]
            if not key.is_exhausted:
                return key
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
                key.client_id[:20],
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
            key.client_id[:20],
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
                self._keys[self._current_index].client_id[:20],
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
                key.access_token = ""  # Force token refresh on new day
                key.token_expires_at = 0.0
                key.last_reset_day = today
                logger.info("Daily reset for OpenSky key '%s'.", key.client_id[:20])

    # ── Status for admin dashboard ───────────────────────────────────────

    def get_status(self) -> KeyManagerStatus:
        """Return a snapshot of all key states for the admin panel."""
        keys_info: list[dict[str, Any]] = []
        for i, key in enumerate(self._keys):
            keys_info.append({
                "index": i,
                "client_id": key.client_id[:20] + ("..." if len(key.client_id) > 20 else ""),
                "source_file": key.source_file,
                "requests_made": key.requests_made,
                "estimated_remaining": max(0, _DAILY_CREDIT_LIMIT - key.requests_made),
                "rate_limit_hits": key.rate_limit_hits,
                "is_exhausted": key.is_exhausted,
                "is_active": i == self._current_index,
                "has_token": bool(key.access_token),
            })

        return KeyManagerStatus(
            total_keys=len(self._keys),
            active_key_index=self._current_index,
            keys=keys_info,
            all_exhausted=self.all_exhausted,
        )


# ── Module-level singleton ──────────────────────────────────────────────────
opensky_key_manager = OpenSkyKeyManager()
