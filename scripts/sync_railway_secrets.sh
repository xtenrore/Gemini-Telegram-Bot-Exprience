#!/bin/sh
set -eu

SERVICE="Gemini-Telegram-Bot-Exprience"
ENVIRONMENT="production"

if [ -z "${RAILWAY_API_TOKEN:-}" ]; then
  echo "Missing required environment variable: RAILWAY_API_TOKEN" >&2
  exit 1
fi
if [ -z "${TELEGRAM_BOT_TOKEN:-}" ]; then
  echo "Missing required environment variable: TELEGRAM_BOT_TOKEN" >&2
  exit 1
fi
if [ -z "${MONGO_URI:-}" ]; then
  echo "Missing required environment variable: MONGO_URI" >&2
  exit 1
fi

# The token created under Project Settings -> Tokens is a Railway project token.
# Railway requires project tokens in RAILWAY_TOKEN and the token itself identifies
# the project/environment, so `railway link` must not be used in CI.
RAILWAY_TOKEN="$RAILWAY_API_TOKEN"
export RAILWAY_TOKEN
unset RAILWAY_API_TOKEN

set_secret() {
  key="$1"
  value="$2"
  printf '%s' "$value" | railway variable set "$key" --stdin --service "$SERVICE" --environment "$ENVIRONMENT" --skip-deploys >/dev/null
  echo "Updated $key"
}

set_optional_secret() {
  key="$1"
  value="$2"
  if [ -n "$value" ]; then
    set_secret "$key" "$value"
  fi
}

set_secret TELEGRAM_BOT_TOKEN "$TELEGRAM_BOT_TOKEN"
set_secret MONGO_URI "$MONGO_URI"
set_optional_secret GEMINI_API_KEY "${GEMINI_API_KEY:-}"
set_optional_secret GROQ_API_KEY "${GROQ_API_KEY:-}"
set_optional_secret OPENSKY_CREDENTIALS_JSON "${OPENSKY_CREDENTIALS_JSON:-}"
set_optional_secret ADMIN_PASSWORD "${ADMIN_PASSWORD:-}"
set_optional_secret ADMIN_TELEGRAM_ID "${ADMIN_TELEGRAM_ID:-}"
set_secret DATABASE_NAME "aircraft_bot"

echo "Railway project-token sync complete. Secret values were not printed."
