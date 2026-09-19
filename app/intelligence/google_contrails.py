"""Cached Google Contrails API v2 forecast sampling for Plane Alerts v4.4.

The hot ADS-B loop never waits on Google. A cache miss schedules one bounded
regional/time/flight-level fetch and immediately returns ``None`` so the
existing deterministic upper-air model remains the fallback. Subsequent cycles
sample the cached NetCDF grid.
"""
from __future__ import annotations

import asyncio
import io
import logging
import math
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_GRID_URL = "https://contrails.googleapis.com/v2/grids"
_CACHE_TTL_S = 45 * 60
_NEGATIVE_TTL_S = 5 * 60
_CACHE_MAX = 192
_INFLIGHT_MAX = 4
_TILE_DEG = 2.0
_SUPPORTED_FL = tuple(range(270, 441, 10))


@dataclass(slots=True, frozen=True)
class GoogleContrailResult:
    probability: float | None
    formation: str
    persistence: str
    confidence: str
    source: str
    flight_level: str
    forecast_time: str
    available: bool = True
    reason: str = ""


@dataclass(slots=True)
class _CacheEntry:
    expires_mono: float
    values: dict[str, Any] | None
    error: str = ""


class GoogleContrailsService:
    def __init__(self) -> None:
        self._cache: "OrderedDict[tuple, _CacheEntry]" = OrderedDict()
        self._inflight: dict[tuple, asyncio.Task[None]] = {}
        self._semaphore = asyncio.Semaphore(_INFLIGHT_MAX)
        self._client: httpx.AsyncClient | None = None

    @staticmethod
    def flight_level(altitude_m: float | None) -> int | None:
        if altitude_m is None:
            return None
        fl = int(round(float(altitude_m) * 3.280839895 / 100.0 / 10.0) * 10)
        if fl < _SUPPORTED_FL[0] or fl > _SUPPORTED_FL[-1]:
            return None
        return min(_SUPPORTED_FL, key=lambda item: abs(item - fl))

    @staticmethod
    def _tile(latitude: float, longitude: float) -> tuple[float, float, float, float]:
        lat0 = math.floor(float(latitude) / _TILE_DEG) * _TILE_DEG
        lon0 = math.floor(float(longitude) / _TILE_DEG) * _TILE_DEG
        return lon0, lat0, min(180.0, lon0 + _TILE_DEG), min(90.0, lat0 + _TILE_DEG)

    @staticmethod
    def _time_bucket(when: datetime | None) -> datetime:
        value = (when or datetime.now(timezone.utc)).astimezone(timezone.utc)
        return value.replace(minute=0, second=0, microsecond=0)

    def _key(self, latitude: float, longitude: float, altitude_m: float, when: datetime | None) -> tuple | None:
        fl = self.flight_level(altitude_m)
        if fl is None:
            return None
        bucket = self._time_bucket(when)
        return (*self._tile(latitude, longitude), fl, bucket.isoformat())

    def _prune(self) -> None:
        now = time.monotonic()
        for key in list(self._cache):
            if self._cache[key].expires_mono <= now:
                self._cache.pop(key, None)
        while len(self._cache) > _CACHE_MAX:
            self._cache.popitem(last=False)
        for key, task in list(self._inflight.items()):
            if task.done():
                self._inflight.pop(key, None)

    def get_or_schedule(
        self,
        latitude: float,
        longitude: float,
        altitude_m: float | None,
        *,
        when: datetime | None = None,
    ) -> GoogleContrailResult | None:
        """Return cached forecast, scheduling background refresh on a miss."""
        api_key = str(getattr(settings, "google_contrails_api_key", "") or "").strip()
        if not api_key or altitude_m is None:
            return None
        key = self._key(latitude, longitude, float(altitude_m), when)
        if key is None:
            return None
        self._prune()
        entry = self._cache.get(key)
        if entry is not None:
            self._cache.move_to_end(key)
            if entry.values is None:
                return None
            probability = self._sample(entry.values, latitude, longitude)
            if probability is None:
                return None
            fl = int(key[4])
            bucket = str(key[5])
            return self._result(probability, fl, bucket)

        if key not in self._inflight and len(self._inflight) < _INFLIGHT_MAX:
            try:
                self._inflight[key] = asyncio.create_task(self._fetch(key, api_key), name=f"google-contrails-fl{key[4]}")
            except RuntimeError:
                pass
        return None

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(8.0, connect=3.0))
        return self._client

    async def _fetch(self, key: tuple, api_key: str) -> None:
        lon0, lat0, lon1, lat1, fl, bucket = key
        try:
            async with self._semaphore:
                client = await self._http()
                params = [
                    ("time", str(bucket)),
                    ("bbox", str(lon0)),
                    ("bbox", str(lat0)),
                    ("bbox", str(lon1)),
                    ("bbox", str(lat1)),
                    ("flightLevel", f"FL{int(fl)}"),
                    ("data", "persistent_formation_probability"),
                ]
                response = await client.get(
                    str(getattr(settings, "google_contrails_grid_url", "") or _GRID_URL),
                    params=params,
                    headers={"X-Goog-Api-Key": api_key},
                )
                response.raise_for_status()
                parsed = self._parse_netcdf(response.content)
                self._cache[key] = _CacheEntry(time.monotonic() + _CACHE_TTL_S, parsed)
                self._cache.move_to_end(key)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._cache[key] = _CacheEntry(time.monotonic() + _NEGATIVE_TTL_S, None, type(exc).__name__)
            logger.warning("google_contrails_fetch_failed fl=%s error=%s", fl, type(exc).__name__)
        finally:
            self._inflight.pop(key, None)
            self._prune()

    @staticmethod
    def _parse_netcdf(content: bytes) -> dict[str, Any]:
        try:
            import netCDF4  # Imported lazily so deterministic fallback survives packaging problems.
            import numpy as np
        except Exception as exc:  # pragma: no cover - deployment dependency guard
            raise RuntimeError("netCDF4 unavailable") from exc

        dataset = netCDF4.Dataset("inmemory.nc", mode="r", memory=content)
        try:
            probability_var = dataset.variables.get("persistent_formation_probability")
            if probability_var is None:
                raise ValueError("persistent_formation_probability missing")
            lat_var = dataset.variables.get("latitude") or dataset.variables.get("lat")
            lon_var = dataset.variables.get("longitude") or dataset.variables.get("lon")
            if lat_var is None or lon_var is None:
                raise ValueError("latitude/longitude coordinates missing")
            probabilities = np.asarray(probability_var[:], dtype=float).squeeze()
            latitudes = np.asarray(lat_var[:], dtype=float).squeeze()
            longitudes = np.asarray(lon_var[:], dtype=float).squeeze()
            return {
                "probabilities": probabilities,
                "latitudes": latitudes,
                "longitudes": longitudes,
            }
        finally:
            dataset.close()

    @staticmethod
    def _sample(values: dict[str, Any], latitude: float, longitude: float) -> float | None:
        import numpy as np

        probs = np.asarray(values["probabilities"], dtype=float)
        lats = np.asarray(values["latitudes"], dtype=float)
        lons = np.asarray(values["longitudes"], dtype=float)
        if probs.size == 0 or lats.size == 0 or lons.size == 0:
            return None

        if lats.ndim == 1 and lons.ndim == 1 and probs.ndim >= 2:
            lat_idx = int(np.nanargmin(np.abs(lats - float(latitude))))
            normalized_lon = float(longitude)
            if np.nanmax(lons) > 180 and normalized_lon < 0:
                normalized_lon += 360.0
            lon_idx = int(np.nanargmin(np.abs(lons - normalized_lon)))
            while probs.ndim > 2:
                probs = probs[0]
            value = float(probs[lat_idx, lon_idx])
        else:
            flat_prob = probs.reshape(-1)
            flat_lat = np.broadcast_to(lats, probs.shape[-lats.ndim:]).reshape(-1) if lats.shape == probs.shape else lats.reshape(-1)
            flat_lon = np.broadcast_to(lons, probs.shape[-lons.ndim:]).reshape(-1) if lons.shape == probs.shape else lons.reshape(-1)
            if flat_lat.size != flat_prob.size or flat_lon.size != flat_prob.size:
                return float(np.nanmean(flat_prob)) if np.isfinite(np.nanmean(flat_prob)) else None
            distance = (flat_lat - float(latitude)) ** 2 + (flat_lon - float(longitude)) ** 2
            value = float(flat_prob[int(np.nanargmin(distance))])

        if not math.isfinite(value):
            return None
        if value > 1.0 and value <= 100.0:
            value /= 100.0
        return max(0.0, min(1.0, value))

    @staticmethod
    def _result(probability: float, fl: int, bucket: str) -> GoogleContrailResult:
        if probability >= 0.78:
            formation, persistence, confidence = "Very Likely", "High", "High"
        elif probability >= 0.58:
            formation, persistence, confidence = "Likely", "High", "High"
        elif probability >= 0.35:
            formation, persistence, confidence = "Possible", "Moderate", "Medium"
        elif probability >= 0.15:
            formation, persistence, confidence = "Unlikely", "Low", "Medium"
        else:
            formation, persistence, confidence = "Very Unlikely", "Low", "Medium"
        return GoogleContrailResult(
            probability=probability,
            formation=formation,
            persistence=persistence,
            confidence=confidence,
            source="Google Contrails v2 forecast",
            flight_level=f"FL{fl}",
            forecast_time=bucket,
        )

    async def close(self) -> None:
        for task in list(self._inflight.values()):
            task.cancel()
        self._inflight.clear()
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()

    def cache_stats(self) -> dict[str, int]:
        self._prune()
        return {"entries": len(self._cache), "inflight": len(self._inflight), "max_entries": _CACHE_MAX}


google_contrails = GoogleContrailsService()
