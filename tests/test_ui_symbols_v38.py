from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup

from app.ui_symbols import monochrome_markup, monochrome_text


def _has_supplementary_emoji(text: str) -> bool:
    return any(0x1F000 <= ord(ch) <= 0x1FAFF for ch in text)


def test_common_plane_emoji_are_monochrome():
    rendered = monochrome_text("📷 Camera ✅ ✈️ flight ☀️ sun 👥 users 🔄 refresh")
    visible = rendered.replace("\ufe0e", "")
    assert visible == "◉ Camera ✓ ✈ flight ☼ sun ○○ users ↻ refresh"
    assert not _has_supplementary_emoji(rendered)
    assert "\ufe0f" not in rendered


def test_unknown_modern_emoji_falls_back_to_neutral_outline():
    rendered = monochrome_text("Status 🫡 ready")
    assert rendered == "Status ◇ ready"
    assert not _has_supplementary_emoji(rendered)


def test_compound_and_skin_tone_emoji_do_not_leak_color_sequences():
    rendered = monochrome_text("Pilot 👨‍✈️ response 👍🏽")
    assert "\u200d" not in rendered
    assert "\ufe0f" not in rendered
    assert not _has_supplementary_emoji(rendered)


def test_inline_keyboard_labels_are_normalised_without_touching_callbacks():
    markup = InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("✅ Accept", callback_data="terms:accept"),
            InlineKeyboardButton("📷 Camera", callback_data="photo:1"),
        ]]
    )
    rendered = monochrome_markup(markup)
    assert rendered.inline_keyboard[0][0].text.replace("\ufe0e", "") == "✓ Accept"
    assert rendered.inline_keyboard[0][0].callback_data == "terms:accept"
    assert rendered.inline_keyboard[0][1].text == "◉ Camera"
    assert rendered.inline_keyboard[0][1].callback_data == "photo:1"


def test_telegram_bot_methods_are_wrapped_once():
    assert getattr(Bot.send_message, "__plane_v38_monochrome__", False)
    assert getattr(Bot.send_photo, "__plane_v38_monochrome__", False)
    assert getattr(Bot.edit_message_media, "__plane_v38_monochrome__", False)
