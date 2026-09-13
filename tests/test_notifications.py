"""Regression tests for Slack notification safety."""

from app.worker.notifications import _safe_slack_text


def test_provider_text_is_safe_for_slack_mrkdwn():
    assert _safe_slack_text("A&B <test>") == "A&amp;B &lt;test&gt;"
    assert _safe_slack_text("normal callsign") == "normal callsign"
