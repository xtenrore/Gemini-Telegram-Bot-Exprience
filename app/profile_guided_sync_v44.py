"""Keep Guided Setup photography changes synchronized with v4.4 profiles."""
from __future__ import annotations

import logging

from app.alert_profiles import sync_active_profile_from_legacy

logger = logging.getLogger(__name__)
_INSTALLED = False


def install_profile_guided_sync_v44() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    from app.photography import telegram as photo_ui

    base_camera = photo_ui.identify_and_save_camera
    base_lens = photo_ui.identify_and_save_lens
    base_spotting = photo_ui.cmd_spotting

    async def camera(user_id: int, user_text: str):
        result = await base_camera(user_id, user_text)
        await sync_active_profile_from_legacy(user_id)
        return result

    async def lens(user_id: int, user_text: str):
        result = await base_lens(user_id, user_text)
        await sync_active_profile_from_legacy(user_id)
        return result

    async def spotting(update, context):
        user = update.effective_user
        result = await base_spotting(update, context)
        if user is not None and context.args:
            try:
                await sync_active_profile_from_legacy(user.id)
            except Exception:
                logger.exception("guided_spotting_profile_sync_failed user=%s", user.id)
        return result

    # `_handle_camera_text` / `_handle_lens_text` resolve these names from the
    # module globals at call time. Registration resolves cmd_spotting after this
    # installer runs, so both Guided interfaces stay on the shared profile model.
    photo_ui.identify_and_save_camera = camera
    photo_ui.identify_and_save_lens = lens
    photo_ui.cmd_spotting = spotting
    _INSTALLED = True
