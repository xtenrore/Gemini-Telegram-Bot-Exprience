"""Regression tests for Telegram notification safety."""

from app.worker.notifications import _safe_provider_text


def test_provider_text_is_safe_for_telegram_html():
    assert _safe_provider_text("A&B <test>") == "A&amp;B &lt;test&gt;"
    assert _safe_provider_text('callsign "quoted"') == "callsign &quot;quoted&quot;"
