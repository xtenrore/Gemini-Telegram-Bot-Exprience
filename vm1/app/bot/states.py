"""Finite State Machine for user conversation states.

Each user has exactly one state at a time, persisted in MongoDB so restarts
don't lose in-progress setup flows.
"""

from __future__ import annotations

import enum
import logging
from typing import Any

from app.database import user_state_col

logger = logging.getLogger(__name__)


class UserState(str, enum.Enum):
    """All possible states a user can be in."""

    IDLE = "idle"
    WAITING_TERMS = "waiting_terms"
    WAITING_LOCATION = "waiting_location"
    WAITING_RADIUS = "waiting_radius"
    WAITING_AIRCRAFT_SELECTION = "waiting_aircraft"
    ADDING_CUSTOM_AIRCRAFT = "adding_custom"


# ── State persistence ────────────────────────────────────────────────────────

async def get_user_state(user_id: int) -> UserState:
    """Retrieve the current state for *user_id*.  Defaults to ``IDLE``."""
    doc = await user_state_col().find_one({"user_id": user_id})
    if doc is None:
        return UserState.IDLE
    try:
        return UserState(doc.get("current_state", "idle"))
    except ValueError:
        return UserState.IDLE


async def set_user_state(
    user_id: int,
    state: UserState,
    temp_data: dict[str, Any] | None = None,
) -> None:
    """Set the conversation state for *user_id*.

    Optionally attach *temp_data* for multi-step flows (e.g. in-progress
    aircraft selection).
    """
    update: dict[str, Any] = {"current_state": state.value}
    if temp_data is not None:
        update["temp_data"] = temp_data

    await user_state_col().update_one(
        {"user_id": user_id},
        {"$set": update},
        upsert=True,
    )
    logger.debug("User %d → state %s", user_id, state.value)


async def get_temp_data(user_id: int) -> dict[str, Any]:
    """Return the temporary data dict for *user_id* (empty dict if none)."""
    doc = await user_state_col().find_one({"user_id": user_id})
    if doc is None:
        return {}
    return doc.get("temp_data", {})


async def update_temp_data(user_id: int, updates: dict[str, Any]) -> None:
    """Merge *updates* into the existing temp_data for *user_id*."""
    await user_state_col().update_one(
        {"user_id": user_id},
        {"$set": {f"temp_data.{k}": v for k, v in updates.items()}},
        upsert=True,
    )


async def clear_user_state(user_id: int) -> None:
    """Reset user to IDLE and wipe temp_data."""
    await set_user_state(user_id, UserState.IDLE, temp_data={})
