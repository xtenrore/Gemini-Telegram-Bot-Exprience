"""Async MongoDB connection and collection helpers via Motor."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from app.config import settings

if TYPE_CHECKING:
    from motor.motor_asyncio import AsyncIOMotorCollection

logger = logging.getLogger(__name__)

# ── Module-level singletons ─────────────────────────────────────────────────
_client: AsyncIOMotorClient | None = None
_db: AsyncIOMotorDatabase | None = None


# ── Lifecycle ────────────────────────────────────────────────────────────────
async def connect_db() -> AsyncIOMotorDatabase:
    """Open the MongoDB connection and return the database handle.

    Also ensures all required indexes exist.
    """
    global _client, _db  # noqa: PLW0603

    logger.info("Connecting to MongoDB at %s …", settings.mongo_uri)
    _client = AsyncIOMotorClient(settings.mongo_uri)
    _db = _client[settings.database_name]

    # Verify connectivity
    await _client.admin.command("ping")
    logger.info("MongoDB connection established – database: %s", settings.database_name)

    await _ensure_indexes(_db)
    return _db


async def close_db() -> None:
    """Close the MongoDB connection."""
    global _client, _db  # noqa: PLW0603

    if _client is not None:
        _client.close()
        _client = None
        _db = None
        logger.info("MongoDB connection closed.")


def get_db() -> AsyncIOMotorDatabase:
    """Return the current database handle (must call ``connect_db`` first)."""
    if _db is None:
        raise RuntimeError("Database not initialised – call connect_db() first.")
    return _db


# ── Collection accessors ────────────────────────────────────────────────────
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


# ── Index creation ──────────────────────────────────────────────────────────
async def _ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    """Create all required indexes (idempotent)."""
    logger.info("Ensuring database indexes …")

    # users
    await db["users"].create_index("user_id", unique=True)

    # locations
    await db["locations"].create_index("user_id")
    await db["locations"].create_index("geohash")

    # preferences
    await db["preferences"].create_index("user_id", unique=True)

    # user_state
    await db["user_state"].create_index("user_id", unique=True)

    # notification_history – compound index for cooldown lookups
    await db["notification_history"].create_index(
        [("user_id", 1), ("aircraft_icao24", 1), ("cooldown_until", 1)]
    )
    # TTL index: automatically delete old notifications after 24 hours
    await db["notification_history"].create_index(
        "cooldown_until", expireAfterSeconds=86400
    )

    # provider_learning – tracks per-user provider selection and learning progress
    await db["provider_learning"].create_index(
        [("user_id", 1), ("geohash", 1)], unique=True
    )
    await db["provider_learning"].create_index("user_id")

    # ai_usage – tracks daily AI usage per model
    await db["ai_usage"].create_index([("model_name", 1), ("day", 1)], unique=True)

    # feedback – tracks user likes/dislikes on notifications
    await db["feedback"].create_index([("user_id", 1), ("notification_id", 1)])
    await db["feedback"].create_index("user_id")

    logger.info("Database indexes ready.")
