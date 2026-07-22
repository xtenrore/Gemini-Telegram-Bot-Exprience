"""Aircraft data providers — 5 sources queried in parallel.

Providers (all free):
  - ADSB.lol       — unlimited, no rate limit, has type data
  - ADSB.fi        — unlimited, 2s spacing, has type data
  - OpenSky        — credit-limited (key rotation), 5s spacing, OAuth2, NO type data
  - Airplanes.Live — unlimited, 2s spacing, has type data
  - ADSB.one       — unlimited, 2s spacing, has type data

Strategy:
  1. During learning: query ALL 5 providers in parallel
  2. After learning: query the learned best + reliable set per user
  3. Merge results by icao24, prefer records with aircraft_type data
"""

from __future__ import annotations

import abc
import asyncio
import logging
import time
from typing import Any

import httpx

from app.aircraft.api_keys import opensky_key_manager
from app.aircraft.models import NormalizedAircraft
from app.config import settings

logger = logging.getLogger(__name__)

# Shared async HTTP client (re-used across requests for connection pooling)
_http_client: httpx.AsyncClient | None = None


async def get_http_client() -> httpx.AsyncClient:
    """Return (and lazily create) a shared ``httpx.AsyncClient``."""
    global _http_client  # noqa: PLW0603
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(timeout=httpx.Timeout(15.0))
    return _http_client


async def close_http_client() -> None:
    """Close the shared HTTP client (call at shutdown)."""
    global _http_client  # noqa: PLW0603
    if _http_client is not None and not _http_client.is_closed:
        await _http_client.aclose()
        _http_client = None


# ── Abstract base ────────────────────────────────────────────────────────────

class AircraftDataProvider(abc.ABC):
    """Interface that every aircraft data source must implement."""

    name: str = "base"
    is_unlimited: bool = True  # Whether this provider has unlimited free requests

    def __init__(self) -> None:
        self.request_count: int = 0
        self.error_count: int = 0
        self.last_request_time: float = 0.0
        self.last_success_time: float = 0.0
        self.last_error: str = ""

    @abc.abstractmethod
    async def get_aircraft_in_area(
        self,
        latitude: float,
        longitude: float,
        radius_nm: int = 250,
    ) -> list[NormalizedAircraft]:
        """Fetch aircraft near *latitude*/*longitude* within *radius_nm* NM."""

    def can_request_now(self) -> bool:
        """Check if we can make a request right now (rate limit check)."""
        return True

    def get_status(self) -> dict[str, Any]:
        """Return provider status for admin dashboard."""
        return {
            "name": self.name,
            "is_unlimited": self.is_unlimited,
            "request_count": self.request_count,
            "error_count": self.error_count,
            "last_request_time": self.last_request_time,
            "last_success_time": self.last_success_time,
            "last_error": self.last_error,
            "can_request_now": self.can_request_now(),
        }


# ── ADSB.lol ─────────────────────────────────────────────────────────────────

class ADSBLolProvider(AircraftDataProvider):
    """Primary provider — unlimited, no rate limits, has type data."""

    name = "adsb.lol"
    is_unlimited = True

    async def get_aircraft_in_area(
        self,
        latitude: float,
        longitude: float,
        radius_nm: int = 250,
    ) -> list[NormalizedAircraft]:
        client = await get_http_client()
        url = f"{settings.adsb_lol_base_url}/point/{latitude}/{longitude}/{radius_nm}"
        logger.debug("[%s] GET %s", self.name, url)

        self.last_request_time = time.time()
        self.request_count += 1

        resp = await client.get(url)
        resp.raise_for_status()
        self.last_success_time = time.time()
        data = resp.json()
        return parse_adsb_response(data)


# ── ADSB.fi ──────────────────────────────────────────────────────────────────

class ADSBFiProvider(AircraftDataProvider):
    """Fallback provider — unlimited, 1 req/s (2s spacing enforced), has type data."""

    name = "adsb.fi"
    is_unlimited = True
    _min_interval: float = 2.0  # seconds

    def can_request_now(self) -> bool:
        return (time.monotonic() - self.last_request_time) >= self._min_interval or self.last_request_time == 0.0

    async def get_aircraft_in_area(
        self,
        latitude: float,
        longitude: float,
        radius_nm: int = 250,
    ) -> list[NormalizedAircraft]:
        # Wait for rate limit window if needed
        elapsed = time.monotonic() - self.last_request_time
        if self.last_request_time > 0 and elapsed < self._min_interval:
            await asyncio.sleep(self._min_interval - elapsed)

        client = await get_http_client()
        url = f"{settings.adsb_fi_base_url}/point/{latitude}/{longitude}/{radius_nm}"
        logger.debug("[%s] GET %s", self.name, url)

        self.last_request_time = time.monotonic()
        self.request_count += 1

        resp = await client.get(url)
        resp.raise_for_status()
        self.last_success_time = time.time()
        data = resp.json()
        return parse_adsb_response(data)


# ── Airplanes.Live ───────────────────────────────────────────────────────────

class AirplanesLiveProvider(AircraftDataProvider):
    """Community provider — free, no key, 2s spacing, has type data.

    Uses the same v2 API format as ADSB.lol / ADSB.fi.
    """

    name = "airplanes.live"
    is_unlimited = True
    _min_interval: float = 2.0

    def can_request_now(self) -> bool:
        return (time.monotonic() - self.last_request_time) >= self._min_interval or self.last_request_time == 0.0

    async def get_aircraft_in_area(
        self,
        latitude: float,
        longitude: float,
        radius_nm: int = 250,
    ) -> list[NormalizedAircraft]:
        elapsed = time.monotonic() - self.last_request_time
        if self.last_request_time > 0 and elapsed < self._min_interval:
            await asyncio.sleep(self._min_interval - elapsed)

        client = await get_http_client()
        url = f"{settings.airplanes_live_base_url}/point/{latitude}/{longitude}/{radius_nm}"
        logger.debug("[%s] GET %s", self.name, url)

        self.last_request_time = time.monotonic()
        self.request_count += 1

        resp = await client.get(url)
        resp.raise_for_status()
        self.last_success_time = time.time()
        data = resp.json()
        return parse_adsb_response(data)


# ── ADSB.one ─────────────────────────────────────────────────────────────────

class ADSBOneProvider(AircraftDataProvider):
    """Community provider — free, no key, 2s spacing, has type data.

    Uses the same v2 API format as ADSB.lol / ADSB.fi.
    """

    name = "adsb.one"
    is_unlimited = True
    _min_interval: float = 2.0

    def can_request_now(self) -> bool:
        return (time.monotonic() - self.last_request_time) >= self._min_interval or self.last_request_time == 0.0

    async def get_aircraft_in_area(
        self,
        latitude: float,
        longitude: float,
        radius_nm: int = 250,
    ) -> list[NormalizedAircraft]:
        elapsed = time.monotonic() - self.last_request_time
        if self.last_request_time > 0 and elapsed < self._min_interval:
            await asyncio.sleep(self._min_interval - elapsed)

        client = await get_http_client()
        url = f"{settings.adsb_one_base_url}/point/{latitude}/{longitude}/{radius_nm}"
        logger.debug("[%s] GET %s", self.name, url)

        self.last_request_time = time.monotonic()
        self.request_count += 1

        resp = await client.get(url)
        resp.raise_for_status()
        self.last_success_time = time.time()
        data = resp.json()
        return parse_adsb_response(data)


# ── OpenSky ──────────────────────────────────────────────────────────────────

class OpenSkyProvider(AircraftDataProvider):
    """Backup provider — credit-limited, OAuth2 key rotation, NO type data."""

    name = "opensky"
    is_unlimited = False
    _min_interval: float = 5.0
    _last_mono: float = 0.0  # monotonic clock for rate limiting

    def can_request_now(self) -> bool:
        if opensky_key_manager.all_exhausted:
            return False
        if not opensky_key_manager.has_keys:
            return False
        return (time.monotonic() - self._last_mono) >= self._min_interval or self._last_mono == 0.0

    async def get_aircraft_in_area(
        self,
        latitude: float,
        longitude: float,
        radius_nm: int = 250,
    ) -> list[NormalizedAircraft]:
        # Get Bearer token via OAuth2
        token = await opensky_key_manager.get_bearer_token()
        if token is None:
            logger.debug("[%s] No available credentials — skipping.", self.name)
            return []

        # Rate limit spacing
        elapsed = time.monotonic() - self._last_mono
        if self._last_mono > 0 and elapsed < self._min_interval:
            await asyncio.sleep(self._min_interval - elapsed)

        # Convert radius to bounding box
        degree_offset = radius_nm / 60.0
        lamin = latitude - degree_offset
        lamax = latitude + degree_offset
        lomin = longitude - degree_offset
        lomax = longitude + degree_offset

        client = await get_http_client()
        url = f"{settings.opensky_base_url}/states/all"
        params = {"lamin": lamin, "lamax": lamax, "lomin": lomin, "lomax": lomax}
        headers = {"Authorization": f"Bearer {token}"}
        logger.debug("[%s] GET %s params=%s", self.name, url, params)

        self._last_mono = time.monotonic()
        self.last_request_time = time.time()
        self.request_count += 1

        try:
            resp = await client.get(url, params=params, headers=headers)

            # Handle 401 — token expired, refresh and retry once
            if resp.status_code == 401:
                logger.info("[%s] Token expired (401), refreshing...", self.name)
                new_token = await opensky_key_manager.refresh_current_token()
                if new_token:
                    headers = {"Authorization": f"Bearer {new_token}"}
                    resp = await client.get(url, params=params, headers=headers)
                else:
                    self.last_error = "Token refresh failed"
                    self.error_count += 1
                    return []

            if resp.status_code == 429:
                opensky_key_manager.mark_rate_limited()
                self.last_error = "Rate limited (HTTP 429)"
                self.error_count += 1
                return []

            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                opensky_key_manager.mark_rate_limited()
            raise

        opensky_key_manager.record_request()
        self.last_success_time = time.time()
        data = resp.json()
        return self._parse(data)

    @staticmethod
    def _parse(data: dict[str, Any]) -> list[NormalizedAircraft]:
        """Parse OpenSky ``/states/all`` response."""
        aircraft_list: list[NormalizedAircraft] = []
        states = data.get("states") or []
        for sv in states:
            if len(sv) < 17:
                continue
            try:
                aircraft_list.append(
                    NormalizedAircraft(
                        icao24=(sv[0] or "").lower().strip(),
                        callsign=(sv[1] or "").strip(),
                        origin_country=sv[2] or "",
                        latitude=sv[6],
                        longitude=sv[5],
                        altitude=sv[7],  # barometric altitude in metres
                        velocity=sv[9],  # ground speed in m/s
                        heading=sv[10],
                        aircraft_type="",  # OpenSky doesn't provide type
                        timestamp=sv[3],
                    )
                )
            except Exception:
                logger.debug("Skipping malformed OpenSky state vector: %s", sv[:4])
        return aircraft_list

    def get_status(self) -> dict[str, Any]:
        status = super().get_status()
        key_status = opensky_key_manager.get_status()
        status["key_rotation"] = {
            "total_keys": key_status.total_keys,
            "active_key_index": key_status.active_key_index,
            "all_exhausted": key_status.all_exhausted,
            "keys": key_status.keys,
        }
        return status


# ── Provider Manager ─────────────────────────────────────────────────────────

class ProviderManager:
    """Query providers in parallel, merge and deduplicate results.

    During learning phase (per user), all 5 providers are queried.
    After learning, queries only the user's learned provider set.
    """

    def __init__(self) -> None:
        self.adsb_lol = ADSBLolProvider()
        self.adsb_fi = ADSBFiProvider()
        self.opensky = OpenSkyProvider()
        self.airplanes_live = AirplanesLiveProvider()
        self.adsb_one = ADSBOneProvider()
        self._all_providers: list[AircraftDataProvider] = [
            self.adsb_lol,
            self.adsb_fi,
            self.opensky,
            self.airplanes_live,
            self.adsb_one,
        ]

    def get_providers_by_names(
        self, names: list[str] | None = None
    ) -> list[AircraftDataProvider]:
        """Return providers filtered by name list, or all if None."""
        if names is None:
            return list(self._all_providers)
        name_set = set(names)
        return [p for p in self._all_providers if p.name in name_set]

    async def query_providers(
        self,
        latitude: float,
        longitude: float,
        radius_nm: int = 250,
        provider_names: list[str] | None = None,
    ) -> tuple[list[NormalizedAircraft], dict[str, list[NormalizedAircraft]]]:
        """Query specified providers (or all), merge and return results.

        Returns:
            Tuple of (merged_aircraft_list, results_by_provider_name)
        """
        providers_to_query = self.get_providers_by_names(provider_names)

        # Query in parallel
        tasks = []
        queried_names = []
        for provider in providers_to_query:
            if not provider.can_request_now():
                logger.debug("Skipping %s (rate limit window)", provider.name)
                continue
            tasks.append(self._safe_query(provider, latitude, longitude, radius_nm))
            queried_names.append(provider.name)

        if not tasks:
            logger.warning("No providers available for this cycle.")
            return [], {}

        results = await asyncio.gather(*tasks)

        # Build results-by-provider dict
        results_by_provider: dict[str, list[NormalizedAircraft]] = {}
        for name, aircraft_list in zip(queried_names, results):
            results_by_provider[name] = aircraft_list

        # Merge all results
        merged = self._merge_results(results_by_provider)

        total_from_all = sum(len(v) for v in results_by_provider.values())
        logger.info(
            "Multi-provider query: %d raw from %d provider(s) -> %d merged unique",
            total_from_all,
            len(results_by_provider),
            len(merged),
        )

        return merged, results_by_provider

    async def _safe_query(
        self,
        provider: AircraftDataProvider,
        latitude: float,
        longitude: float,
        radius_nm: int,
    ) -> list[NormalizedAircraft]:
        """Query a single provider with error handling."""
        try:
            result = await provider.get_aircraft_in_area(latitude, longitude, radius_nm)
            logger.debug(
                "Provider %s returned %d aircraft", provider.name, len(result)
            )
            return result
        except Exception as exc:
            provider.error_count += 1
            provider.last_error = str(exc)
            logger.warning("Provider %s failed: %s", provider.name, exc)
            return []

    @staticmethod
    def _merge_results(
        results_by_provider: dict[str, list[NormalizedAircraft]],
    ) -> list[NormalizedAircraft]:
        """Merge and deduplicate aircraft from all providers.

        When multiple providers report the same aircraft (by icao24),
        prefer the record that has aircraft_type data (ADSB.lol/fi have it,
        OpenSky does not).  Also merge in origin_country from OpenSky if
        the other providers lack it.
        """
        merged: dict[str, NormalizedAircraft] = {}

        for provider_name, aircraft_list in results_by_provider.items():
            for ac in aircraft_list:
                if not ac.has_position:
                    continue

                existing = merged.get(ac.icao24)
                if existing is None:
                    # First time seeing this aircraft
                    merged[ac.icao24] = ac
                else:
                    # Merge: prefer the record with more data
                    # Priority: aircraft_type > origin_country > newer timestamp
                    if ac.aircraft_type and not existing.aircraft_type:
                        # New record has type data, old doesn't — use new as base
                        # but keep origin_country from old if new lacks it
                        if not ac.origin_country and existing.origin_country:
                            ac = ac.model_copy(
                                update={"origin_country": existing.origin_country}
                            )
                        merged[ac.icao24] = ac
                    elif not ac.aircraft_type and existing.aircraft_type:
                        # Old record has type, new doesn't — keep old but merge country
                        if ac.origin_country and not existing.origin_country:
                            merged[ac.icao24] = existing.model_copy(
                                update={"origin_country": ac.origin_country}
                            )
                    else:
                        # Both have type or both lack it — merge origin_country
                        if ac.origin_country and not existing.origin_country:
                            merged[ac.icao24] = existing.model_copy(
                                update={"origin_country": ac.origin_country}
                            )

        return list(merged.values())

    def get_all_provider_status(self) -> list[dict[str, Any]]:
        """Return status dicts for all providers (for admin dashboard)."""
        return [p.get_status() for p in self._all_providers]


# ── Response parsing ─────────────────────────────────────────────────────────

def parse_adsb_response(data: dict[str, Any]) -> list[NormalizedAircraft]:
    """Parse the ADSB.lol / ADSB.fi / Airplanes.Live / ADSB.one v2 JSON response."""
    aircraft_list: list[NormalizedAircraft] = []
    for ac in data.get("ac", []):
        try:
            aircraft_list.append(
                NormalizedAircraft(
                    icao24=ac.get("hex", "").lower().strip(),
                    callsign=(ac.get("flight") or "").strip(),
                    origin_country="",  # Not in this API
                    latitude=ac.get("lat"),
                    longitude=ac.get("lon"),
                    altitude=_feet_to_metres(ac.get("alt_baro")),
                    velocity=_knots_to_ms(ac.get("gs")),
                    heading=ac.get("track"),
                    aircraft_type=(ac.get("t") or "").strip().upper(),
                    timestamp=ac.get("seen_pos"),
                )
            )
        except Exception:
            logger.debug("Skipping malformed aircraft record: %s", ac)
    return aircraft_list


# ── Unit conversion helpers ──────────────────────────────────────────────────

def _feet_to_metres(feet: Any) -> float | None:
    """Convert feet to metres.  Returns None for non-numeric inputs."""
    if feet is None:
        return None
    try:
        value = float(feet)
    except (TypeError, ValueError):
        return None
    return round(value * 0.3048, 1)


def _knots_to_ms(knots: Any) -> float | None:
    """Convert knots to m/s.  Returns None for non-numeric inputs."""
    if knots is None:
        return None
    try:
        value = float(knots)
    except (TypeError, ValueError):
        return None
    return round(value * 0.514444, 1)
