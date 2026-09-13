"""Unit tests for the Slack command-layer helpers."""

from app.slack_app import _match_category, slack_user_key


def test_slack_user_key_is_stable_negative_integer():
    first = slack_user_key("U012ABCDEF")
    second = slack_user_key("U012ABCDEF")
    other = slack_user_key("U999XYZ")
    assert isinstance(first, int)
    assert first < 0
    assert first == second
    assert first != other


def test_category_matching_is_case_insensitive():
    assert _match_category("military") == "Military"
    assert _match_category("  large   airliners ") == "Large Airliners"
    assert _match_category("not-a-category") is None
