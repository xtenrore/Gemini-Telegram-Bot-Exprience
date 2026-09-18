"""Plane? v3.8 monochrome iconography for Telegram-facing UI.

Telegram and mobile OSes aggressively render many Unicode pictographs as
full-colour emoji.  Plane? v3.8 keeps the compact visual cues but normalises
outgoing text to a restrained black/white, text-glyph language instead.

This module is deliberately centralised: existing message templates can keep
working, while every outbound Telegram text/caption/button passes through the
same renderer.  That prevents old or rarely-used flows from leaking colourful
emoji back into the interface.
"""
from __future__ import annotations

import copy
import re
from functools import wraps
from typing import Any, Callable

from telegram import Bot, InlineKeyboardMarkup

TEXT_VARIATION = "\ufe0e"
EMOJI_VARIATION = "\ufe0f"
ZWJ = "\u200d"

# Small, line-oriented glyphs chosen to read like UI pictograms rather than
# expressive emoji.  Keep these semantic enough that the adjacent text label
# remains clear even when a platform/font substitutes a slightly different
# monochrome shape.
_REPLACEMENTS: dict[str, str] = {
    "✈️": "✈︎",
    "✈": "✈︎",
    "🛩️": "✈︎",
    "🛩": "✈︎",
    "🚁": "⌁",
    "📡": "⌁",
    "🛰️": "⌁",
    "🛰": "⌁",
    "📷": "◉",
    "📸": "◉",
    "🔭": "⌕",
    "📍": "⌖",
    "🎯": "◎",
    "🌐": "⊕",
    "🌍": "⊙",
    "📋": "≡",
    "📖": "≡",
    "✏️": "✎︎",
    "✏": "✎︎",
    "✅": "✓",
    "☑️": "✓",
    "☑": "✓",
    "✔️": "✓",
    "✔": "✓",
    "❌": "×",
    "❎": "×",
    "⚠️": "△",
    "⚠": "△",
    "🚀": "↗",
    "🎉": "✦",
    "⚙️": "⚙︎",
    "⚙": "⚙︎",
    "📦": "□",
    "💼": "▣",
    "🏛️": "◫",
    "🏛": "◫",
    "🔬": "⌕",
    "⭐": "☆",
    "🌟": "☆",
    "⬅️": "←",
    "⬅": "←",
    "⏭️": "»",
    "⏭": "»",
    "🗑️": "⌫",
    "🗑": "⌫",
    "👍": "↑",
    "👎": "↓",
    "👥": "○○",
    "⚡": "ϟ",
    "🛡️": "◇",
    "🛡": "◇",
    "🔕": "⊘",
    "🔔": "◌",
    "🔄": "↻",
    "↪️": "↪︎",
    "↪": "↪︎",
    "🔥": "▲",
    "⏱️": "◷",
    "⏱": "◷",
    "☀️": "☼︎",
    "☀": "☼︎",
    "🌤️": "☼︎",
    "🌤": "☼︎",
    "🌙": "☾︎",
    "🌡️": "°",
    "🌡": "°",
    "👁️": "◉",
    "👁": "◉",
    "💨": "≋",
    "🌫️": "≋",
    "🌫": "≋",
    "♨️": "≋",
    "♨": "≋",
    "💡": "◇",
    "❤️": "♡",
    "❤": "♡",
    "❗": "!",
    "❓": "?",
    "⭕": "○",
}

# Supplementary pictographs include modern emoji, faces, flags, transport,
# objects and symbols.  Anything not explicitly mapped falls back to a neutral
# outline diamond so no colourful emoji can leak through dynamically.
_SUPPLEMENTARY_EMOJI_RE = re.compile(r"[\U0001F000-\U0001FAFF]")
_SKIN_TONE_RE = re.compile(r"[\U0001F3FB-\U0001F3FF]")
_MULTIPLE_FALLBACK_RE = re.compile(r"◇{2,}")

# Common BMP symbol blocks can also default to colourful emoji presentation.
# VS15 explicitly asks the renderer for text presentation.
_BMP_EMOJI_STYLE_RE = re.compile(r"([\u2600-\u26FF\u2700-\u27BF])(?!\ufe0e)")


def monochrome_text(value: Any) -> Any:
    """Return *value* with colourful emoji normalised to monochrome glyphs.

    Non-string values are returned unchanged so this can be safely applied to
    optional Telegram parameters.
    """
    if not isinstance(value, str) or not value:
        return value

    text = value
    for source, target in sorted(_REPLACEMENTS.items(), key=lambda item: len(item[0]), reverse=True):
        text = text.replace(source, target)

    # Remove emoji presentation / joining metadata before the generic fallback.
    text = text.replace(EMOJI_VARIATION, "").replace(ZWJ, "")
    text = _SKIN_TONE_RE.sub("", text)
    text = _SUPPLEMENTARY_EMOJI_RE.sub("◇", text)
    text = _MULTIPLE_FALLBACK_RE.sub("◇", text)

    # Force remaining classic symbol/dingbat code points to text presentation.
    text = _BMP_EMOJI_STYLE_RE.sub(lambda m: m.group(1) + TEXT_VARIATION, text)
    return text


def monochrome_markup(markup: Any) -> Any:
    """Clone an InlineKeyboardMarkup with monochrome button labels."""
    if not isinstance(markup, InlineKeyboardMarkup):
        return markup

    changed = False
    rows = []
    for row in markup.inline_keyboard:
        new_row = []
        for button in row:
            new_text = monochrome_text(button.text)
            if new_text == button.text:
                new_row.append(button)
                continue
            try:
                clone = copy.copy(button)
                object.__setattr__(clone, "text", new_text)
                new_row.append(clone)
                changed = True
            except Exception:
                # Styling must never break a working interaction. If a future PTB
                # object becomes non-copyable, leave that one button untouched.
                new_row.append(button)
        rows.append(new_row)

    return InlineKeyboardMarkup(rows) if changed else markup


def _monochrome_media(media: Any) -> Any:
    if isinstance(media, list):
        return [_monochrome_media(item) for item in media]
    if isinstance(media, tuple):
        return tuple(_monochrome_media(item) for item in media)
    caption = getattr(media, "caption", None)
    if not isinstance(caption, str):
        return media
    try:
        clone = copy.copy(media)
        object.__setattr__(clone, "caption", monochrome_text(caption))
        return clone
    except Exception:
        return media


def _patch_bot_method(name: str, text_fields: tuple[str, ...]) -> None:
    original = getattr(Bot, name, None)
    if original is None or getattr(original, "__plane_v38_monochrome__", False):
        return

    @wraps(original)
    async def wrapped(self: Bot, *args: Any, **kwargs: Any) -> Any:
        for field in text_fields:
            if field in kwargs:
                kwargs[field] = monochrome_text(kwargs[field])
        if "reply_markup" in kwargs:
            kwargs["reply_markup"] = monochrome_markup(kwargs["reply_markup"])
        if "media" in kwargs:
            kwargs["media"] = _monochrome_media(kwargs["media"])
        return await original(self, *args, **kwargs)

    setattr(wrapped, "__plane_v38_monochrome__", True)
    setattr(Bot, name, wrapped)


_INSTALLED = False


def install_telegram_monochrome() -> None:
    """Install the v3.8 outbound Telegram styling layer once per process."""
    global _INSTALLED
    if _INSTALLED:
        return

    # Text-bearing Telegram methods used by Plane?, plus nearby media methods so
    # future features inherit the same no-colour-emoji rule automatically.
    method_fields: dict[str, tuple[str, ...]] = {
        "send_message": ("text",),
        "edit_message_text": ("text",),
        "answer_callback_query": ("text",),
        "send_photo": ("caption",),
        "edit_message_caption": ("caption",),
        "send_video": ("caption",),
        "send_animation": ("caption",),
        "send_audio": ("caption",),
        "send_document": ("caption",),
        "send_voice": ("caption",),
        "edit_message_media": (),
        "send_media_group": (),
    }
    for method_name, fields in method_fields.items():
        _patch_bot_method(method_name, fields)

    _INSTALLED = True
