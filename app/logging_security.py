"""Production logging hardening for credentials and provider secrets."""
from __future__ import annotations

import logging
from collections.abc import Iterable

from app.config import settings

_REDACTED = "[REDACTED]"


class SecretRedactionFilter(logging.Filter):
    """Remove configured secret values from rendered log records."""

    def __init__(self, secrets: Iterable[str]) -> None:
        super().__init__()
        self._secrets = tuple(
            secret for secret in secrets if isinstance(secret, str) and len(secret.strip()) >= 6
        )

    def filter(self, record: logging.LogRecord) -> bool:
        if not self._secrets:
            return True
        try:
            message = record.getMessage()
            for secret in self._secrets:
                message = message.replace(secret, _REDACTED)
            record.msg = message
            record.args = ()
        except Exception:
            # Logging must never make the application fail.
            pass
        return True


def configure_secure_logging() -> None:
    """Configure application logging without provider credentials in output."""
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    )

    # httpx logs full request URLs at INFO. Telegram embeds the bot token in its
    # API URL, so production request tracing must never run at INFO.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    redactor = SecretRedactionFilter(
        (
            settings.telegram_bot_token,
            settings.mongo_uri,
            settings.gemini_api_key,
            settings.groq_api_key,
            settings.webhook_secret,
            settings.admin_password,
            settings.opensky_credentials_json,
        )
    )
    for handler in logging.getLogger().handlers:
        handler.addFilter(redactor)
