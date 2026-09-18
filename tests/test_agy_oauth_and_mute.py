from app.agy_console import _controls_keyboard, _extract_google_urls
from app.agy_state import is_agy_console_active, set_agy_console_active
from app.worker.v36 import _control


def test_cursed_terminal_oauth_link_becomes_clean_google_url():
    raw = (
        "terminal-markup 8;;https://accounts.google.com/o/oauth2/v2/auth"
        "?client_id=test&redirect_uri=http%3A%2F%2Flocalhost8;;Open-link"
    )
    urls = _extract_google_urls([raw])
    assert urls == [
        "https://accounts.google.com/o/oauth2/v2/auth"
        "?client_id=test&redirect_uri=http%3A%2F%2Flocalhost"
    ]


def test_non_google_url_is_not_promoted_to_oauth_button():
    assert _extract_google_urls(["https://example.com/not-google"]) == []


def test_google_oauth_button_uses_clean_url():
    url = "https://accounts.google.com/o/oauth2/v2/auth?client_id=test"
    markup = _controls_keyboard(url)
    assert markup.inline_keyboard[0][0].text == "🔐 Open Google sign-in"
    assert markup.inline_keyboard[0][0].url == url
    assert markup.inline_keyboard[1][0].callback_data == "agy:key:enter"


def test_agy_console_state_temporarily_mutes_monitor_notifications():
    user_id = 424242
    user = {"user_id": user_id, "admin_control": {"notifications_enabled": True}}
    set_agy_console_active(user_id, False)
    assert _control(user)["notifications_enabled"] is True

    set_agy_console_active(user_id, True)
    try:
        assert is_agy_console_active(user_id) is True
        assert _control(user)["notifications_enabled"] is False
    finally:
        set_agy_console_active(user_id, False)

    assert _control(user)["notifications_enabled"] is True


def test_agy_mute_does_not_override_saved_manual_pause():
    user_id = 424243
    user = {"user_id": user_id, "admin_control": {"notifications_enabled": False}}
    set_agy_console_active(user_id, False)
    assert _control(user)["notifications_enabled"] is False
