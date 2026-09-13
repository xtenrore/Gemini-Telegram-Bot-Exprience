"""Async MongoDB connection and collection helpers via Motor."""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from app.config import settings

if TYPE_CHECKING:
    from motor.motor_asyncio import AsyncIOMotorCollection

logger = logging.getLogger(__name__)
_client: AsyncIOMotorClient | None = None
_db: AsyncIOMotorDatabase | None = None


def _mongo_target_label(uri: str) -> str:
    """Return a credential-free Mongo target label for logs."""
    try:
        parsed = urlsplit(uri)
        host = parsed.hostname or "unknown-host"
        scheme = parsed.scheme or "mongodb"
        return f"{scheme}://{host}"
    except Exception:
        return "mongodb://configured-host"


async def connect_db(
    max_retries: int = 5,
    retry_delay: float = 2.0,
    timeout_ms: int = 10000,
) -> AsyncIOMotorDatabase:
    """Connect to MongoDB/Atlas with conservative retry and memory settings."""
    global _client, _db
    target = _mongo_target_label(settings.mongo_uri)
    for attempt in range(1, max_retries + 1):
        try:
            logger.info("Connecting to MongoDB at %s (attempt %d/%d) …", target, attempt, max_retries)
            _client = AsyncIOMotorClient(
                settings.mongo_uri,
                serverSelectionTimeoutMS=timeout_ms,
                connectTimeoutMS=timeout_ms,
                maxPoolSize=20,
                minPoolSize=0,
                maxIdleTimeMS=60000,
                appname="aircraft-alert-v3.2",
            )
            _db = _client[settings.database_name]
            await _client.admin.command("ping")
            logger.info("MongoDB connection established – database: %s", settings.database_name)
            await _ensure_indexes(_db)
            return _db
        except Exception as exc:
            if _client is not None:
                _client.close()
                _client = None
                _db = None
            if attempt < max_retries:
                logger.warning("Failed to connect to MongoDB (%s). Retrying in %.1fs...", type(exc).__name__, retry_delay)
                await asyncio.sleep(retry_delay)
            else:
                logger.error("Could not connect to MongoDB after %d attempts: %s", max_retries, type(exc).__name__)
                raise


async def close_db() -> None:
    global _client, _db
    if _client is not None:
        _client.close()
        _client = None
        _db = None
        logger.info("MongoDB connection closed.")


def get_db() -> AsyncIOMotorDatabase:
    if _db is None:
        raise RuntimeError("Database not initialised – call connect_db() first.")
    return _db


def users_col() -> AsyncIOMotorCollection:
    return get_db()["users"]


def locations_col() -> AsyncIOMotorCollection:
    return get_db()["locations"]


def preferences_col() -> AsyncIOMotorCollection:
    return get_db()["preferences"]


def notification_history_col() -> AsyncIOMotorCollection:
    return get_db()["notification_history"]


def user_state_col() -> AsyncIOMotorCollection:
    return get_db()["user_state"]


def provider_learning_col() -> AsyncIOMotorCollection:
    return get_db()["provider_learning"]


def ai_usage_col() -> AsyncIOMotorCollection:
    return get_db()["ai_usage"]


def feedback_col() -> AsyncIOMotorCollection:
    return get_db()["feedback"]


def camera_profiles_col() -> AsyncIOMotorCollection:
    return get_db()["camera_profiles"]


def system_status_col() -> AsyncIOMotorCollection:
    return get_db()["system_status"]


async def _ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    logger.info("Ensuring database indexes …")
    await db["users"].create_index("user_id", unique=True)
    await db["locations"].create_index("user_id")
    await db["locations"].create_index("geohash")
    await db["preferences"].create_index("user_id", unique=True)
    await db["user_state"].create_index("user_id", unique=True)
    await db["notification_history"].create_index(
        [("user_id", 1), ("aircraft_icao24", 1), ("cooldown_until", 1)]
    )
    await db["notification_history"].create_index("cooldown_until", expireAfterSeconds=86400)
    await db["provider_learning"].create_index(
        [("user_id", 1), ("geohash", 1)], unique=True
    )
    await db["provider_learning"].create_index("user_id")
    await db["ai_usage"].create_index([("model_name", 1), ("day", 1)], unique=True)
    await db["feedback"].create_index([("user_id", 1), ("notification_id", 1)])
    await db["feedback"].create_index("user_id")
    await db["camera_profiles"].create_index("user_id", unique=True)
    await db["photo_alert_snapshots"].create_index([("user_id", 1), ("aircraft_icao24", 1)])
    await db["photo_alert_snapshots"].create_index("expires_at", expireAfterSeconds=0)
    await db["system_status"].create_index("updated_at")
    logger.info("Database indexes ready.")
