"""Finite State Machine for user conversation states."""
from __future__ import annotations

import enum
import logging
from typing import Any

from app.database import user_state_col

logger = logging.getLogger(__name__)


class UserState(str, enum.Enum):
    IDLE = "idle"
    WAITING_TERMS = "waiting_terms"
    WAITING_LOCATION = "waiting_location"
    WAITING_RADIUS = "waiting_radius"
    WAITING_AIRCRAFT_SELECTION = "waiting_aircraft"
    ADDING_CUSTOM_AIRCRAFT = "adding_custom"
    WAITING_CAMERA = "waiting_camera"
    WAITING_LENS = "waiting_lens"


async def get_user_state(user_id: int) -> UserState:
    doc = await user_state_col().find_one({"user_id": user_id})
    if doc is None:
        return UserState.IDLE
    try:
        return UserState(doc.get("current_state", "idle"))
    except ValueError:
        return UserState.IDLE


async def set_user_state(user_id: int, state: UserState, temp_data: dict[str, Any] | None = None) -> None:
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
    doc = await user_state_col().find_one({"user_id": user_id})
    return doc.get("temp_data", {}) if doc else {}


async def update_temp_data(user_id: int, updates: dict[str, Any]) -> None:
    await user_state_col().update_one(
        {"user_id": user_id},
        {"$set": {f"temp_data.{k}": v for k, v in updates.items()}},
        upsert=True,
    )


async def clear_user_state(user_id: int) -> None:
    await set_user_state(user_id, UserState.IDLE, temp_data={})
