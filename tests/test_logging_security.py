from __future__ import annotations

import logging

from app.logging_security import SecretRedactionFilter


def test_secret_redaction_filter_masks_plain_and_formatted_values() -> None:
    secret = "123456789:telegram-secret-value"
    redactor = SecretRedactionFilter([secret])

    record = logging.LogRecord(
        name="httpx",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="POST https://api.telegram.org/bot%s/getMe",
        args=(secret,),
        exc_info=None,
    )

    assert redactor.filter(record) is True
    rendered = record.getMessage()
    assert secret not in rendered
    assert "[REDACTED]" in rendered


def test_secret_redaction_filter_ignores_empty_and_tiny_values() -> None:
    redactor = SecretRedactionFilter(["", "abc"])
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="abc should remain because tiny strings are unsafe to redact globally",
        args=(),
        exc_info=None,
    )
    assert redactor.filter(record) is True
    assert "abc should remain" in record.getMessage()
