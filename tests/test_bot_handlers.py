"""Tests for Telegram bot keyboards, messages, and state helpers."""

from app.bot.keyboards import (
    aircraft_categories_keyboard,
    category_types_sub_keyboard,
    custom_aircraft_keyboard,
    notification_feedback_keyboard,
    terms_keyboard,
)
from app.bot.messages import (
    aircraft_alert_message,
    setup_complete_message,
    status_message,
)
from app.bot.states import UserState


def test_keyboards_structure():
    """Verify inline keyboards build valid Telegram button structures."""
    kb_terms = terms_keyboard()
    assert len(kb_terms.inline_keyboard) == 1
    assert len(kb_terms.inline_keyboard[0]) == 2

    kb_cats = aircraft_categories_keyboard(
        selected_cats={"Military"},
        disabled_types=set(),
        custom_aircraft=["B738"],
    )
    assert len(kb_cats.inline_keyboard) > 0

    kb_sub = category_types_sub_keyboard("Military", disabled_types={"C17"})
    assert len(kb_sub.inline_keyboard) > 0

    kb_custom = custom_aircraft_keyboard(["B738", "A320"])
    assert len(kb_custom.inline_keyboard) > 0

    kb_feedback = notification_feedback_keyboard("notif-12345")
    assert len(kb_feedback.inline_keyboard) == 1
    assert len(kb_feedback.inline_keyboard[0]) == 2


def test_messages_formatting():
    """Verify HTML message generation."""
    msg = aircraft_alert_message(
        aircraft_type="B738",
        callsign="UAL456",
        distance_km=8.5,
        altitude_m=3000.0,
        velocity_ms=180.0,
        heading=90.0,
        icao24="a12345",
        origin_country="United States",
        eta_seconds=45.0,
    )
    assert "B738" in msg
    assert "UAL456" in msg
    assert "8.5 km" in msg
    assert "Arriving in" in msg

    status_msg = status_message(
        selected_categories=["Military"],
        custom_aircraft=["B738"],
        lat=51.5,
        lon=-0.12,
        radius_km=15.0,
        setup_complete=True,
    )
    assert "Military" in status_msg
    assert "B738" in status_msg
    assert "Monitoring active" in status_msg

    setup_msg = setup_complete_message(
        selected_categories=["Cargo"],
        custom_aircraft=[],
        lat=51.5,
        lon=-0.12,
        radius_km=20.0,
    )
    assert "Setup complete" in setup_msg


def test_user_state_constants():
    """Verify user state enumeration."""
    assert UserState.IDLE == "idle"
    assert UserState.WAITING_TERMS == "waiting_terms"
    assert UserState.WAITING_LOCATION == "waiting_location"
    assert UserState.WAITING_RADIUS == "waiting_radius"
    assert UserState.WAITING_AIRCRAFT_SELECTION == "waiting_aircraft"
    assert UserState.ADDING_CUSTOM_AIRCRAFT == "adding_custom"
