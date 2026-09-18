"""Shared in-process state for the private AGY Telegram console.

Aircraft/photo notifications are muted while a user is actively attached to the
AGY console. This state is intentionally process-local so a crashed/restarted
bot cannot leave notifications permanently muted.
"""
from __future__ import annotations

_active_user_ids: set[int] = set()


def set_agy_console_active(user_id: int, active: bool) -> None:
    user_id = int(user_id)
    if active:
        _active_user_ids.add(user_id)
    else:
        _active_user_ids.discard(user_id)


def is_agy_console_active(user_id: int) -> bool:
    return int(user_id) in _active_user_ids
