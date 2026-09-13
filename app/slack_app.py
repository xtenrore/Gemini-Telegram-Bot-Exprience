"""Slack Socket Mode interface for the aircraft alert service."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from datetime import datetime, timezone
from typing import Any

from slack_bolt.async_app import AsyncApp
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler

from app.aircraft.categories import CATEGORY_ORDER, validate_icao_code
from app.aircraft.ai_judge import ai_judge
from app.aircraft.learner import provider_learner
from app.config import settings
from app.database import feedback_col, locations_col, notification_history_col, preferences_col, users_col
from app.worker.geo import compute_geohash

logger = logging.getLogger(__name__)

_slack_app: AsyncApp | None = None
_socket_handler: AsyncSocketModeHandler | None = None
_socket_task: asyncio.Task[Any] | None = None
_FEEDBACK_RE = re.compile(r"^feedback_(like|dislike):(.+)$")
_MENTION_RE = re.compile(r"<@[^>]+>")


def slack_user_key(slack_user_id: str) -> int:
    """Map a Slack user ID to a stable negative 63-bit integer."""
    digest = hashlib.sha256(slack_user_id.encode("utf-8")).digest()
    value = int.from_bytes(digest[:8], "big") & ((1 << 63) - 1)
    return -(value or 1)


def slack_configured() -> bool:
    return bool(settings.slack_bot_token.strip() and settings.slack_app_token.strip())


def _match_category(text: str) -> str | None:
    normalized = " ".join(text.strip().lower().split())
    for category in CATEGORY_ORDER:
        if category.lower() == normalized:
            return category
    return None


async def _touch_user(slack_user_id: str, channel_id: str) -> int:
    user_id = slack_user_key(slack_user_id)
    now = datetime.now(timezone.utc)
    await users_col().update_one(
        {"user_id": user_id},
        {
            "$set": {
                "platform": "slack",
                "slack_user_id": slack_user_id,
                "notification_channel_id": channel_id,
                "last_active": now,
            },
            "$setOnInsert": {
                "user_id": user_id,
                "username": slack_user_id,
                "first_name": "",
                "setup_complete": False,
                "terms_accepted": False,
                "paused": False,
                "created_at": now,
            },
        },
        upsert=True,
    )
    return user_id


async def _refresh_setup_complete(user_id: int) -> bool:
    user = await users_col().find_one({"user_id": user_id}) or {}
    loc = await locations_col().find_one({"user_id": user_id})
    prefs = await preferences_col().find_one({"user_id": user_id}) or {}
    has_targets = bool(prefs.get("selected_categories") or prefs.get("custom_aircraft"))
    complete = bool(user.get("terms_accepted") and not user.get("paused", False) and loc and has_targets)
    await users_col().update_one({"user_id": user_id}, {"$set": {"setup_complete": complete}})
    return complete


def _help_text() -> str:
    categories = ", ".join(CATEGORY_ORDER)
    return (
        "*Aircraft Alert — Slack commands*\n"
        "`start` — start or reset the guided setup\n"
        "`accept` — accept the aircraft-data disclaimer\n"
        "`location <lat>,<lon>` — set your monitoring location\n"
        "`radius <km>` — set radius from 1 to 150 km\n"
        "`watch <category or ICAO>` — add a category/type\n"
        "`unwatch <category or ICAO>` — remove a category/type\n"
        "`categories` — list built-in aircraft categories\n"
        "`status` — show your current configuration\n"
        "`pause` / `resume` — stop or resume alerts\n"
        "`help` — show this message\n\n"
        f"*Categories:* {categories}\n"
        "Example: `watch Military`, `watch B738`, `location 41.0082,28.9784`"
    )


async def _status_text(user_id: int) -> str:
    user = await users_col().find_one({"user_id": user_id}) or {}
    loc = await locations_col().find_one({"user_id": user_id})
    prefs = await preferences_col().find_one({"user_id": user_id}) or {}
    categories = prefs.get("selected_categories", [])
    custom = prefs.get("custom_aircraft", [])
    lines = ["*Aircraft Alert status*"]
    lines.append(f"Monitoring: {'✅ active' if user.get('setup_complete') else '⏸️ inactive'}")
    lines.append(f"Terms: {'accepted' if user.get('terms_accepted') else 'not accepted'}")
    if loc:
        lines.append(f"Location: `{loc.get('latitude', 0):.4f}, {loc.get('longitude', 0):.4f}`")
        lines.append(f"Radius: {loc.get('radius_km', settings.default_radius_km):.0f} km")
    else:
        lines.append("Location: not set")
    lines.append("Categories: " + (", ".join(categories) if categories else "none"))
    lines.append("Custom ICAO: " + (", ".join(custom) if custom else "none"))
    if user.get("paused"):
        lines.append("Alerts are manually paused.")
    return "\n".join(lines)


async def _send(client: Any, channel_id: str, text: str, blocks: list[dict] | None = None) -> None:
    await client.chat_postMessage(channel=channel_id, text=text, blocks=blocks, unfurl_links=False, unfurl_media=False)


async def _handle_command(*, slack_user_id: str, channel_id: str, text: str, client: Any) -> None:
    user_id = await _touch_user(slack_user_id, channel_id)
    command = " ".join((text or "").strip().split())
    if not command:
        await _send(client, channel_id, _help_text())
        return
    head, _, arg = command.partition(" ")
    head = head.lower()
    arg = arg.strip()

    if head in {"help", "commands", "?"}:
        await _send(client, channel_id, _help_text())
        return
    if head == "categories":
        await _send(client, channel_id, "*Built-in aircraft categories*\n• " + "\n• ".join(CATEGORY_ORDER))
        return
    if head in {"start", "setup"}:
        await users_col().update_one(
            {"user_id": user_id},
            {"$set": {"platform": "slack", "slack_user_id": slack_user_id, "notification_channel_id": channel_id, "terms_accepted": False, "setup_complete": False, "paused": False}},
        )
        await preferences_col().delete_one({"user_id": user_id})
        await locations_col().delete_one({"user_id": user_id})
        await _send(client, channel_id, "✈️ *Aircraft Alert setup*\n\nAircraft data can be delayed, incomplete, or unavailable; some aircraft do not transmit ADS-B. This service is informational only and must not be used for navigation or safety-critical decisions.\n\nType `accept` to continue.")
        return
    if head == "accept":
        await users_col().update_one({"user_id": user_id}, {"$set": {"terms_accepted": True, "paused": False}})
        await _refresh_setup_complete(user_id)
        await _send(client, channel_id, "✅ Disclaimer accepted.\nNext send `location <latitude>,<longitude>` — for example `location 41.0082,28.9784`.")
        return
    if head == "location":
        if not arg:
            await _send(client, channel_id, "Use `location <latitude>,<longitude>`, for example `location 41.0082,28.9784`.")
            return
        try:
            pieces = [p.strip() for p in re.split(r"[,\s]+", arg) if p.strip()]
            if len(pieces) != 2:
                raise ValueError
            lat, lon = float(pieces[0]), float(pieces[1])
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                raise ValueError
        except ValueError:
            await _send(client, channel_id, "❌ Invalid coordinates.")
            return
        old = await locations_col().find_one({"user_id": user_id}) or {}
        radius = float(old.get("radius_km", settings.default_radius_km))
        geohash = compute_geohash(lat, lon)
        await locations_col().update_one(
            {"user_id": user_id},
            {"$set": {"user_id": user_id, "latitude": lat, "longitude": lon, "radius_km": radius, "geohash": geohash, "updated_at": datetime.now(timezone.utc)}},
            upsert=True,
        )
        await _refresh_setup_complete(user_id)
        await _send(client, channel_id, f"✅ Location saved: `{lat:.4f}, {lon:.4f}`. Radius is {radius:.0f} km.\nNow add something to monitor, e.g. `watch Military` or `watch B738`.")
        return
    if head == "radius":
        try:
            radius = float(arg)
            if not 1 <= radius <= 150:
                raise ValueError
        except ValueError:
            await _send(client, channel_id, "❌ Radius must be from 1 to 150 km.")
            return
        result = await locations_col().update_one({"user_id": user_id}, {"$set": {"radius_km": radius, "updated_at": datetime.now(timezone.utc)}})
        if result.matched_count == 0:
            await _send(client, channel_id, "Set a location first with `location <lat>,<lon>`.")
            return
        await _refresh_setup_complete(user_id)
        await _send(client, channel_id, f"✅ Monitoring radius set to {radius:g} km.")
        return
    if head in {"watch", "unwatch"}:
        if not arg:
            await _send(client, channel_id, f"Use `{head} <category or ICAO>`. Type `categories` for the list.")
            return
        prefs = await preferences_col().find_one({"user_id": user_id}) or {}
        categories = list(prefs.get("selected_categories", []))
        custom = list(prefs.get("custom_aircraft", []))
        category = _match_category(arg)
        code = arg.upper().replace(" ", "")
        if category:
            if head == "watch" and category not in categories:
                categories.append(category)
            if head == "unwatch" and category in categories:
                categories.remove(category)
            label = category
        elif validate_icao_code(code):
            if head == "watch" and code not in custom:
                custom.append(code)
            if head == "unwatch" and code in custom:
                custom.remove(code)
            label = code
        else:
            await _send(client, channel_id, "❌ I don't recognize that aircraft category/type. Type `categories` or use a 2–4 character ICAO type code.")
            return
        await preferences_col().update_one(
            {"user_id": user_id},
            {"$set": {"user_id": user_id, "selected_categories": categories, "custom_aircraft": custom, "disabled_types": [], "updated_at": datetime.now(timezone.utc)}},
            upsert=True,
        )
        active = await _refresh_setup_complete(user_id)
        verb = "Watching" if head == "watch" else "Removed"
        suffix = " Monitoring is active." if active else ""
        await _send(client, channel_id, f"✅ {verb} *{label}*.{suffix}")
        return
    if head == "status":
        await _refresh_setup_complete(user_id)
        await _send(client, channel_id, await _status_text(user_id))
        return
    if head == "pause":
        await users_col().update_one({"user_id": user_id}, {"$set": {"paused": True, "setup_complete": False}})
        await _send(client, channel_id, "⏸️ Aircraft alerts paused.")
        return
    if head == "resume":
        await users_col().update_one({"user_id": user_id}, {"$set": {"paused": False}})
        active = await _refresh_setup_complete(user_id)
        if active:
            await _send(client, channel_id, "▶️ Aircraft alerts resumed.")
        else:
            await _send(client, channel_id, "I can't resume yet because setup is incomplete. Type `status` to see what's missing.")
        return
    await _send(client, channel_id, f"I don't know `{head}` yet. Type `help` for the available commands.")


async def _handle_feedback(ack: Any, body: dict, client: Any, logger: Any) -> None:
    await ack()
    action = (body.get("actions") or [{}])[0]
    match = _FEEDBACK_RE.match(action.get("action_id", ""))
    if not match:
        return
    feedback, notif_id = match.groups()
    slack_user_id = (body.get("user") or {}).get("id", "")
    if not slack_user_id:
        return
    user_id = slack_user_key(slack_user_id)
    await feedback_col().update_one(
        {"user_id": user_id, "notification_id": notif_id},
        {"$set": {"user_id": user_id, "notification_id": notif_id, "feedback": feedback, "updated_at": datetime.now(timezone.utc)}},
        upsert=True,
    )
    channel_id = (body.get("channel") or {}).get("id")
    if feedback == "like":
        if channel_id:
            await _send(client, channel_id, "👍 Feedback recorded — thank you.")
        return
    loc = await locations_col().find_one({"user_id": user_id})
    geohash = loc.get("geohash", "") if loc else ""
    if geohash:
        try:
            await provider_learner.trigger_relearning(user_id=user_id, geohash=geohash, extra_planes=settings.relearn_plane_count)
        except Exception:
            logger.exception("Could not trigger provider relearning")
    analysis = ""
    notif = await notification_history_col().find_one({"_id": notif_id})
    if notif and ai_judge.can_call():
        try:
            analysis = await ai_judge.analyze_dislike(
                icao24=notif.get("aircraft_icao24", ""),
                aircraft_type=notif.get("aircraft_type", "Unknown"),
                distance_km=float(notif.get("distance_km", 0.0)),
                providers_reporting=notif.get("reporting_providers", []),
                user_feedback="Slack user marked this aircraft alert as not useful",
            )
        except Exception:
            logger.exception("Gemini dislike analysis failed")
    text = "👎 Feedback recorded. I'll run extra provider checks for this area to improve future alerts."
    if analysis and analysis != "UNKNOWN":
        text += f"\n🤖 Gemini overview: {analysis}"
    if channel_id:
        await _send(client, channel_id, text)


def build_slack_app() -> AsyncApp:
    global _slack_app
    if _slack_app is not None:
        return _slack_app
    _slack_app = AsyncApp(token=settings.slack_bot_token)

    @_slack_app.event("message")
    async def _on_message(event: dict, client: Any, logger: Any) -> None:
        if event.get("subtype") or event.get("bot_id") or event.get("channel_type") != "im":
            return
        user, channel = event.get("user"), event.get("channel")
        if not user or not channel:
            return
        try:
            await _handle_command(slack_user_id=user, channel_id=channel, text=event.get("text", ""), client=client)
        except Exception:
            logger.exception("Slack DM command failed")
            await _send(client, channel, "⚠️ Something went wrong. Please try again.")

    @_slack_app.event("app_mention")
    async def _on_mention(event: dict, client: Any, logger: Any) -> None:
        user, channel = event.get("user"), event.get("channel")
        if not user or not channel:
            return
        text = _MENTION_RE.sub("", event.get("text", "")).strip()
        try:
            await _handle_command(slack_user_id=user, channel_id=channel, text=text, client=client)
        except Exception:
            logger.exception("Slack mention command failed")
            await _send(client, channel, "⚠️ Something went wrong. Please try again.")

    _slack_app.action(_FEEDBACK_RE)(_handle_feedback)
    return _slack_app


async def start_slack_socket() -> asyncio.Task[Any] | None:
    global _socket_handler, _socket_task
    if not slack_configured():
        logger.warning("SLACK_BOT_TOKEN/SLACK_APP_TOKEN not configured; Slack interface disabled.")
        return None
    if _socket_task and not _socket_task.done():
        return _socket_task
    app = build_slack_app()
    _socket_handler = AsyncSocketModeHandler(app, settings.slack_app_token)
    _socket_task = asyncio.create_task(_socket_handler.start_async(), name="slack-socket-mode")
    await asyncio.sleep(0)
    logger.info("Slack Socket Mode task started.")
    return _socket_task


async def stop_slack_socket() -> None:
    global _socket_handler, _socket_task
    if _socket_handler is not None:
        try:
            await _socket_handler.close_async()
        except Exception:
            logger.exception("Error while closing Slack Socket Mode")
    if _socket_task and not _socket_task.done():
        _socket_task.cancel()
        try:
            await _socket_task
        except asyncio.CancelledError:
            pass
    _socket_handler = None
    _socket_task = None
