#!/bin/sh
set -eu

# Railway should never start a seemingly healthy bot with empty production
# credentials. The previous deployment silently fell back to localhost MongoDB
# and disabled Telegram/Gemini because no runtime variables were present.
if [ -n "${RAILWAY_ENVIRONMENT:-}" ]; then
  missing=""
  [ -n "${TELEGRAM_BOT_TOKEN:-}" ] || missing="$missing TELEGRAM_BOT_TOKEN"
  [ -n "${MONGO_URI:-}" ] || missing="$missing MONGO_URI"
  [ -n "${GEMINI_API_KEY:-}" ] || missing="$missing GEMINI_API_KEY"

  if [ -n "$missing" ]; then
    echo "FATAL: Railway runtime configuration is incomplete. Missing:$missing" >&2
    echo "Refusing to start so the deployment cannot look healthy while Telegram, MongoDB, or Gemini are disabled." >&2
    exit 78
  fi

  echo "Railway runtime configuration check passed: Telegram, MongoDB, and Gemini credentials are present."
fi

exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --workers 1
