"""Slack aircraft notifications with feedback buttons and DM fallback."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient

from app.aircraft.models import NormalizedAircraft
from app.config import settings
from app.database import users_col
from app.worker.geo import heading_to_cardinal, metres_to_feet, ms_to_knots

logger = logging.getLogger(__name__)
_client: AsyncWebClient | None = None
_send_semaphore = asyncio.Semaphore(20)


def _safe_slack_text(value: str) -> str:
    return (value or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _get_client() -> AsyncWebClient:
    global _client
    if _client is None:
        _client = AsyncWebClient(token=settings.slack_bot_token)
    return _client


async def _dm_channel(slack_user_id: str) -> str:
    response = await _get_client().conversations_open(users=slack_user_id)
    return response["channel"]["id"]


def _alert_text(aircraft: NormalizedAircraft, distance_km: float, eta_seconds: float | None) -> str:
    lines = [f"🚀 *Early Warning Alert!* Arriving in ~{int(eta_seconds)}s"] if eta_seconds is not None and eta_seconds > 0 else ["✈️ *Aircraft Alert!*"]
    aircraft_type = _safe_slack_text(aircraft.display_type or "Unknown")
    callsign = _safe_slack_text(aircraft.callsign or "")
    country = _safe_slack_text(aircraft.origin_country or "")
    icao24 = (aircraft.icao24 or "").strip().lower()
    lines.append(f"*Type:* `{aircraft_type}`")
    if callsign:
        lines.append(f"*Callsign:* `{callsign}`")
    lines.append(f"*Distance:* {distance_km:.1f} km away")
    if aircraft.altitude is not None:
        lines.append(f"*Altitude:* {aircraft.altitude:,.0f} m ({metres_to_feet(aircraft.altitude):,} ft)")
    if aircraft.velocity is not None:
        lines.append(f"*Speed:* {aircraft.velocity:.0f} m/s ({ms_to_knots(aircraft.velocity)} kt)")
    if aircraft.heading is not None:
        lines.append(f"*Heading:* {heading_to_cardinal(aircraft.heading)} ({aircraft.heading:.0f}°)")
    if country:
        lines.append(f"*Origin:* {country}")
    if icao24:
        lines.append(f"<https://globe.adsb.fi/?icao={icao24}|🌍 Track on ADSB.fi>")
    return "\n".join(lines)


async def send_aircraft_notification(user_id: int, aircraft: NormalizedAircraft, distance_km: float, notification_id: str = "", eta_seconds: float | None = None) -> bool:
    if not settings.slack_bot_token.strip():
        logger.warning("SLACK_BOT_TOKEN missing; cannot send aircraft notification")
        return False
    user = await users_col().find_one({"user_id": user_id, "platform": "slack"}) or {}
    slack_user_id = user.get("slack_user_id", "")
    channel_id = user.get("notification_channel_id", "") or settings.slack_alert_channel_id
    if not channel_id and slack_user_id:
        try:
            channel_id = await _dm_channel(slack_user_id)
        except SlackApiError:
            logger.exception("Could not open Slack DM for user %s", slack_user_id)
            return False
    if not channel_id:
        logger.warning("Slack destination missing for internal user %s", user_id)
        return False
    text = _alert_text(aircraft, distance_km, eta_seconds)
    blocks: list[dict[str, Any]] = [{"type": "section", "text": {"type": "mrkdwn", "text": text}}]
    if notification_id:
        blocks.append({"type": "actions", "elements": [
            {"type": "button", "text": {"type": "plain_text", "text": "👍 Useful"}, "action_id": f"feedback_like:{notification_id}", "value": notification_id},
            {"type": "button", "text": {"type": "plain_text", "text": "👎 Not useful"}, "action_id": f"feedback_dislike:{notification_id}", "value": notification_id},
        ]})
    async with _send_semaphore:
        try:
            await _get_client().chat_postMessage(channel=channel_id, text=text, blocks=blocks, unfurl_links=False, unfurl_media=False)
            logger.info("Slack aircraft notification sent to internal user %s", user_id)
            return True
        except SlackApiError as exc:
            code = exc.response.get("error", "")
            if code in {"channel_not_found", "not_in_channel", "is_archived"} and slack_user_id:
                try:
                    dm = await _dm_channel(slack_user_id)
                    await _get_client().chat_postMessage(channel=dm, text=text, blocks=blocks, unfurl_links=False, unfurl_media=False)
                    await users_col().update_one({"user_id": user_id}, {"$set": {"notification_channel_id": dm}})
                    return True
                except SlackApiError:
                    logger.exception("Slack DM fallback failed for user %s", slack_user_id)
            elif code in {"account_inactive", "user_not_found"}:
                await users_col().update_one({"user_id": user_id}, {"$set": {"setup_complete": False}})
            logger.error("Slack send failed for internal user %s: %s", user_id, code)
            return False
        except Exception:
            logger.exception("Unexpected Slack notification failure")
            return False


async def send_admin_alert(text: str) -> None:
    if not settings.slack_bot_token.strip():
        logger.warning("Admin alert (Slack token missing): %s", text)
        return
    channel_id = settings.slack_alert_channel_id.strip()
    if not channel_id and settings.admin_slack_user_id.strip():
        try:
            channel_id = await _dm_channel(settings.admin_slack_user_id.strip())
        except Exception:
            logger.exception("Could not open admin Slack DM")
            return
    if not channel_id:
        logger.warning("Admin alert (no Slack destination configured): %s", text)
        return
    try:
        await _get_client().chat_postMessage(channel=channel_id, text=f"🔔 *Admin Alert*\n{_safe_slack_text(text)}", unfurl_links=False)
    except Exception:
        logger.exception("Failed to send Slack admin alert")
