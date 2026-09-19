#!/bin/sh
set -eu

# Railway should never start a seemingly healthy bot with empty core production
# credentials. Gemini is deliberately optional in Plane Alerts: deterministic
# trajectory, alert, camera and environment intelligence must continue without AI.
if [ -n "${RAILWAY_ENVIRONMENT:-}" ]; then
  missing=""
  [ -n "${TELEGRAM_BOT_TOKEN:-}" ] || missing="$missing TELEGRAM_BOT_TOKEN"
  [ -n "${MONGO_URI:-}" ] || missing="$missing MONGO_URI"

  if [ -n "$missing" ]; then
    echo "FATAL: Railway runtime configuration is incomplete. Missing:$missing" >&2
    echo "Refusing to start so the deployment cannot look healthy while Telegram or MongoDB are disabled." >&2
    exit 78
  fi

  if [ -n "${GEMINI_API_KEY:-}" ]; then
    echo "Railway runtime configuration check passed: Telegram and MongoDB are configured; optional Gemini advisor is enabled."
  else
    echo "Railway runtime configuration check passed: Telegram and MongoDB are configured; Gemini advisor is disabled and deterministic fallback remains active."
  fi
fi

# The Dockerfile uses this script as CMD, so this is the authoritative Railway
# production entrypoint. Keep it on the v4.4 composition layer rather than the
# legacy base app, otherwise Telegram-visible v4.4 handlers/routes are skipped.
exec uvicorn app.main_v44:app --host 0.0.0.0 --port "${PORT:-8000}" --workers 1
