"""OpenSky API credential rotation with OAuth2 token support.

Hosted deployments can provide one credential pair in each ``OPENSKY_1`` through
``OPENSKY_5`` secret. Each slot accepts either JSON with ``clientId`` and
``clientSecret`` or the compact ``clientId:clientSecret`` form. The legacy
``OPENSKY_CREDENTIALS_JSON`` aggregate remains supported.
Local development may still use untracked JSON files under ``API_KEYS_DIR``.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_DAILY_CREDIT_LIMIT = 4000


@dataclass
class OpenSkyKey:
    client_id: str
    client_secret: str
    source_file: str
    access_token: str = ""
    token_expires_at: float = 0.0
    requests_made: int = 0
    rate_limit_hits: int = 0
    last_rate_limited_at: float = 0.0
    is_exhausted: bool = False
    last_reset_day: int = 0


@dataclass
class KeyManagerStatus:
    total_keys: int = 0
    active_key_index: int = 0
    keys: list[dict] = field(default_factory=list)
    all_exhausted: bool = False


class OpenSkyKeyManager:
    """Rotate OpenSky credentials and cache OAuth2 bearer tokens."""

    def __init__(self) -> None:
        self._keys: list[OpenSkyKey] = []
        self._current_index = 0
        self._token_lock = asyncio.Lock()

    @staticmethod
    def _parse_credential(data: dict[str, Any], source: str) -> OpenSkyKey | None:
        client_id = str(data.get("clientId") or data.get("username") or "").strip()
        client_secret = str(data.get("clientSecret") or data.get("password") or "").strip()
        if not client_id or not client_secret:
            logger.warning("Skipping OpenSky credential from %s: missing client id/secret", source)
            return None
        return OpenSkyKey(client_id=client_id, client_secret=client_secret, source_file=source)

    def _parse_secret_slot(self, raw: str, source: str) -> list[OpenSkyKey]:
        value = (raw or "").strip()
        if not value:
            return []

        try:
            payload = json.loads(value)
        except json.JSONDecodeError:
            payload = None

        if isinstance(payload, dict):
            key = self._parse_credential(payload, source)
            return [key] if key else []
        if isinstance(payload, list):
            keys: list[OpenSkyKey] = []
            for index, item in enumerate(payload, start=1):
                if isinstance(item, dict):
                    key = self._parse_credential(item, f"{source}:{index}")
                    if key:
                        keys.append(key)
            return keys

        # Convenient GitHub-secret form: OPENSKY_1=id:secret
        if ":" in value:
            client_id, client_secret = value.split(":", 1)
            key = self._parse_credential(
                {"clientId": client_id, "clientSecret": client_secret}, source
            )
            return [key] if key else []

        logger.warning(
            "Skipping OpenSky credential from %s: expected JSON or clientId:clientSecret",
            source,
        )
        return []

    def _load_env_credentials(self) -> list[OpenSkyKey]:
        keys: list[OpenSkyKey] = []
        slots = (
            ("OPENSKY_1", settings.opensky_1),
            ("OPENSKY_2", settings.opensky_2),
            ("OPENSKY_3", settings.opensky_3),
            ("OPENSKY_4", settings.opensky_4),
            ("OPENSKY_5", settings.opensky_5),
        )
        for label, raw in slots:
            keys.extend(self._parse_secret_slot(raw, label))

        legacy = settings.opensky_credentials_json.strip()
        if legacy:
            try:
                payload = json.loads(legacy)
            except json.JSONDecodeError as exc:
                logger.error("OPENSKY_CREDENTIALS_JSON is invalid JSON: %s", exc)
                payload = []
            if isinstance(payload, dict):
                payload = [payload]
            if isinstance(payload, list):
                for index, item in enumerate(payload, start=1):
                    if not isinstance(item, dict):
                        logger.warning("Skipping non-object OpenSky credential at index %d", index)
                        continue
                    key = self._parse_credential(item, f"OPENSKY_CREDENTIALS_JSON:{index}")
                    if key:
                        keys.append(key)
            elif payload:
                logger.error("OPENSKY_CREDENTIALS_JSON must be a JSON object or array")

        # A slot may duplicate a credential still present in the legacy aggregate.
        unique: list[OpenSkyKey] = []
        seen: set[tuple[str, str]] = set()
        for key in keys:
            signature = (key.client_id, key.client_secret)
            if signature in seen:
                continue
            seen.add(signature)
            unique.append(key)
        return unique

    def _load_file_credentials(self) -> list[OpenSkyKey]:
        keys_dir = Path(settings.api_keys_dir)
        if not keys_dir.exists():
            return []

        keys: list[OpenSkyKey] = []
        for fpath in sorted(keys_dir.glob("*.json")):
            try:
                data = json.loads(fpath.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Failed to read OpenSky credential %s: %s", fpath.name, exc)
                continue
            if isinstance(data, dict):
                key = self._parse_credential(data, fpath.name)
                if key:
                    keys.append(key)
        return keys

    def load_keys(self) -> int:
        """Load credentials, preferring environment-backed secrets."""
        self._keys = self._load_env_credentials()
        if self._keys:
            logger.info("Loaded %d OpenSky credential(s) from environment", len(self._keys))
        else:
            self._keys = self._load_file_credentials()
            if self._keys:
                logger.info("Loaded %d OpenSky credential(s) from local untracked files", len(self._keys))
            else:
                logger.info("No OpenSky credentials configured; OpenSky fallback disabled")

        self._current_index = 0
        return len(self._keys)

    async def get_bearer_token(self) -> str | None:
        if not self._keys:
            return None
        self._maybe_reset_daily_counters()

        async with self._token_lock:
            for _ in range(len(self._keys)):
                key = self._find_available_key()
                if key is None:
                    return None
                if key.access_token and time.monotonic() < key.token_expires_at - 300:
                    return key.access_token
                token = await self._acquire_token(key)
                if token:
                    return token
                if not key.is_exhausted:
                    # Network/server failure is not evidence that another credential is better.
                    return None
            return None

    async def _acquire_token(self, key: OpenSkyKey) -> str | None:
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
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            logger.warning(
                "OpenSky token rejected credential %s with HTTP %s",
                key.source_file,
                status,
            )
            if status in (400, 401, 403, 429):
                key.is_exhausted = True
                if status == 429:
                    key.rate_limit_hits += 1
                    key.last_rate_limited_at = time.time()
                self._rotate_to_next()
            return None
        except Exception as exc:
            logger.warning("Failed to acquire OpenSky token for credential %s: %s", key.source_file, type(exc).__name__)
            return None

        access_token = str(data.get("access_token") or "")
        if not access_token:
            logger.warning("OpenSky token response missing access_token for %s", key.source_file)
            return None

        try:
            expires_in = int(data.get("expires_in", 1800))
        except (TypeError, ValueError):
            expires_in = 1800
        key.access_token = access_token
        key.token_expires_at = time.monotonic() + max(60, expires_in)
        return access_token

    async def refresh_current_token(self) -> str | None:
        key = self._find_available_key()
        if key is None:
            return None
        key.access_token = ""
        key.token_expires_at = 0.0
        async with self._token_lock:
            token = await self._acquire_token(key)
            if token:
                return token
            replacement = self._find_available_key()
            if replacement is None or replacement is key:
                return None
            return await self._acquire_token(replacement)

    def get_current_credentials(self) -> tuple[str, str] | None:
        key = self._find_available_key()
        return None if key is None else (key.client_id, key.client_secret)

    def _find_available_key(self) -> OpenSkyKey | None:
        if not self._keys:
            return None
        checked = 0
        while checked < len(self._keys):
            key = self._keys[self._current_index]
            if not key.is_exhausted:
                return key
            self._current_index = (self._current_index + 1) % len(self._keys)
            checked += 1
        return None

    @property
    def has_keys(self) -> bool:
        return bool(self._keys)

    @property
    def all_exhausted(self) -> bool:
        return not self._keys or all(key.is_exhausted for key in self._keys)

    def record_request(self) -> None:
        if not self._keys:
            return
        key = self._keys[self._current_index]
        key.requests_made += 1
        if key.requests_made >= _DAILY_CREDIT_LIMIT - 50:
            key.is_exhausted = True
            self._rotate_to_next()

    def mark_rate_limited(self) -> None:
        if not self._keys:
            return
        key = self._keys[self._current_index]
        key.rate_limit_hits += 1
        key.last_rate_limited_at = time.time()
        key.is_exhausted = True
        self._rotate_to_next()

    def _rotate_to_next(self) -> None:
        if not self._keys:
            return
        original = self._current_index
        attempts = 0
        while attempts < len(self._keys):
            self._current_index = (self._current_index + 1) % len(self._keys)
            if not self._keys[self._current_index].is_exhausted:
                if self._current_index != original:
                    logger.info("Rotated to OpenSky credential %s", self._keys[self._current_index].source_file)
                return
            attempts += 1

    def _maybe_reset_daily_counters(self) -> None:
        today = datetime.datetime.now(datetime.timezone.utc).timetuple().tm_yday
        for key in self._keys:
            if key.last_reset_day == today:
                continue
            key.requests_made = 0
            key.rate_limit_hits = 0
            key.is_exhausted = False
            key.access_token = ""
            key.token_expires_at = 0.0
            key.last_reset_day = today

    def get_status(self) -> KeyManagerStatus:
        self._maybe_reset_daily_counters()
        keys_info: list[dict[str, Any]] = []
        for index, key in enumerate(self._keys):
            keys_info.append(
                {
                    "index": index,
                    "client_id": key.client_id[:8] + "…" if len(key.client_id) > 8 else key.client_id,
                    "source_file": key.source_file,
                    "requests_made": key.requests_made,
                    "estimated_remaining": max(0, _DAILY_CREDIT_LIMIT - key.requests_made),
                    "rate_limit_hits": key.rate_limit_hits,
                    "is_exhausted": key.is_exhausted,
                    "is_active": index == self._current_index,
                    "has_token": bool(key.access_token),
                }
            )
        return KeyManagerStatus(
            total_keys=len(self._keys),
            active_key_index=self._current_index,
            keys=keys_info,
            all_exhausted=self.all_exhausted,
        )


opensky_key_manager = OpenSkyKeyManager()
