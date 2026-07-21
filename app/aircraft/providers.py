"""Aircraft data providers with smart multi-source querying.

Strategy:
  1. Query ALL providers in parallel (respecting each one's rate limits)
  2. Merge results by icao24 (prefer records with aircraft_type data)
  3. Score provider coverage per geohash region
  4. Learn which provider(s) work best for each area
  5. Prefer unlimited providers (ADSB.lol/fi) over credit-limited OpenSky

Providers:
  - ADSB.lol  — unlimited, no rate limit, has type data
  - ADSB.fi   — unlimited, 1 req/s (2s spacing enforced), has type data
  - OpenSky   — credit-limited (key rotation), 5s spacing, NO type data
"""

from __future__ import annotations

import abc
import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from app.aircraft.api_keys import opensky_key_manager
from app.aircraft.models import NormalizedAircraft
from app.config import settings
from app.database import provider_coverage_col

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


# ── OpenSky ──────────────────────────────────────────────────────────────────

class OpenSkyProvider(AircraftDataProvider):
    """Backup provider — credit-limited, key rotation, NO type data in responses."""

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
        creds = opensky_key_manager.get_current_credentials()
        if creds is None:
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
        auth = creds
        logger.debug("[%s] GET %s params=%s", self.name, url, params)

        self._last_mono = time.monotonic()
        self.last_request_time = time.time()
        self.request_count += 1

        try:
            resp = await client.get(url, params=params, auth=auth)
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


# ── Provider Manager — smart multi-source ────────────────────────────────────

class ProviderManager:
    """Query ALL providers in parallel, merge results, and learn coverage.

    The manager tracks which providers detect aircraft in each geohash
    region so it can make increasingly efficient queries over time.
    """

    # After this many cycles, we have enough data to determine best providers
    LEARNING_CYCLES = 10

    def __init__(self) -> None:
        self.adsb_lol = ADSBLolProvider()
        self.adsb_fi = ADSBFiProvider()
        self.opensky = OpenSkyProvider()
        self._providers: list[AircraftDataProvider] = [
            self.adsb_lol,
            self.adsb_fi,
            self.opensky,
        ]
        # In-memory coverage scores: geohash -> {provider_name: plane_count}
        self._coverage_scores: dict[str, dict[str, int]] = {}
        self._cycle_count: dict[str, int] = {}  # geohash -> cycles observed

    async def query_all_providers(
        self,
        latitude: float,
        longitude: float,
        radius_nm: int = 250,
        geohash: str = "",
    ) -> list[NormalizedAircraft]:
        """Query all available providers, merge and deduplicate results.

        Returns a merged list of aircraft from all sources.
        Also updates coverage scoring for the given geohash region.
        """
        # Determine which providers to query this cycle
        providers_to_query = self._select_providers(geohash)

        # Query in parallel
        tasks = []
        provider_names = []
        for provider in providers_to_query:
            if not provider.can_request_now():
                logger.debug("Skipping %s (rate limit window)", provider.name)
                continue
            tasks.append(self._safe_query(provider, latitude, longitude, radius_nm))
            provider_names.append(provider.name)

        if not tasks:
            logger.warning("No providers available for this cycle.")
            return []

        results = await asyncio.gather(*tasks)

        # Build results-by-provider dict for coverage scoring
        results_by_provider: dict[str, list[NormalizedAircraft]] = {}
        for name, aircraft_list in zip(provider_names, results):
            results_by_provider[name] = aircraft_list

        # Update coverage scores
        if geohash:
            self._update_coverage(geohash, results_by_provider)
            # Persist to database (fire-and-forget)
            asyncio.create_task(self._persist_coverage(geohash))

        # Merge all results
        merged = self._merge_results(results_by_provider)

        total_from_all = sum(len(v) for v in results_by_provider.values())
        logger.info(
            "Multi-provider query: %d raw from %d provider(s) -> %d merged unique",
            total_from_all,
            len(results_by_provider),
            len(merged),
        )

        return merged

    def _select_providers(self, geohash: str) -> list[AircraftDataProvider]:
        """Choose which providers to query based on learned coverage.

        During learning phase (first N cycles), query ALL providers.
        After learning, prioritize based on coverage scores but still
        include supplementary providers occasionally.
        """
        cycles = self._cycle_count.get(geohash, 0)

        if cycles < self.LEARNING_CYCLES:
            # Learning phase: query everything
            return list(self._providers)

        # Post-learning: check coverage scores
        scores = self._coverage_scores.get(geohash, {})
        if not scores:
            return list(self._providers)

        # Always include unlimited providers that have coverage
        selected: list[AircraftDataProvider] = []
        for p in self._providers:
            p_score = scores.get(p.name, 0)
            if p.is_unlimited and p_score > 0:
                selected.append(p)

        # Include OpenSky only if it uniquely detects planes the others miss
        opensky_score = scores.get("opensky", 0)
        unlimited_total = sum(scores.get(p.name, 0) for p in self._providers if p.is_unlimited)
        if opensky_score > 0 and opensky_score > unlimited_total * 0.1:
            # OpenSky detects >10% more planes than unlimited providers
            if self.opensky not in selected:
                selected.append(self.opensky)

        # Every 5th cycle, query all providers to re-calibrate
        if cycles % 5 == 0:
            return list(self._providers)

        return selected if selected else list(self._providers)

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

    def _update_coverage(
        self,
        geohash: str,
        results_by_provider: dict[str, list[NormalizedAircraft]],
    ) -> None:
        """Update in-memory coverage scores for a region."""
        if geohash not in self._coverage_scores:
            self._coverage_scores[geohash] = {}
            self._cycle_count[geohash] = 0

        self._cycle_count[geohash] += 1

        for provider_name, aircraft_list in results_by_provider.items():
            # Count unique icao24s with valid positions
            unique_ids = {ac.icao24 for ac in aircraft_list if ac.has_position}
            current = self._coverage_scores[geohash].get(provider_name, 0)
            # Exponential moving average to adapt over time
            alpha = 0.3
            self._coverage_scores[geohash][provider_name] = int(
                alpha * len(unique_ids) + (1 - alpha) * current
            )

    async def _persist_coverage(self, geohash: str) -> None:
        """Save coverage scores to MongoDB for persistence across restarts."""
        try:
            scores = self._coverage_scores.get(geohash, {})
            cycles = self._cycle_count.get(geohash, 0)
            await provider_coverage_col().update_one(
                {"geohash": geohash},
                {
                    "$set": {
                        "scores": scores,
                        "cycles": cycles,
                        "updated_at": datetime.now(timezone.utc),
                    }
                },
                upsert=True,
            )
        except Exception:
            logger.debug("Failed to persist coverage for %s", geohash)

    async def load_coverage_from_db(self) -> None:
        """Load persisted coverage scores from MongoDB on startup."""
        try:
            cursor = provider_coverage_col().find({})
            async for doc in cursor:
                gh = doc.get("geohash", "")
                if gh:
                    self._coverage_scores[gh] = doc.get("scores", {})
                    self._cycle_count[gh] = doc.get("cycles", 0)
            logger.info(
                "Loaded coverage data for %d region(s) from database.",
                len(self._coverage_scores),
            )
        except Exception:
            logger.debug("No existing coverage data found in database.")

    def get_all_provider_status(self) -> list[dict[str, Any]]:
        """Return status dicts for all providers (for admin dashboard)."""
        return [p.get_status() for p in self._providers]

    def get_coverage_summary(self) -> dict[str, Any]:
        """Return coverage score summary for admin dashboard."""
        return {
            "regions_tracked": len(self._coverage_scores),
            "scores": dict(self._coverage_scores),
            "cycles": dict(self._cycle_count),
        }


# ── Response parsing ─────────────────────────────────────────────────────────

def parse_adsb_response(data: dict[str, Any]) -> list[NormalizedAircraft]:
    """Parse the ADSB.lol / ADSB.fi v2 JSON response format."""
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
