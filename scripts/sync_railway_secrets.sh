#!/bin/sh
set -eu

SERVICE="Gemini-Telegram-Bot-Exprience"
ENVIRONMENT="production"
PROJECT_ID="167a00b7-c56c-4cbb-9f24-3daded12443c"

require_env() {
  name="$1"
  eval "value=\${$name:-}"
  if [ -z "$value" ]; then
    echo "Missing required environment variable: $name" >&2
    exit 1
  fi
}

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

require_env RAILWAY_API_TOKEN
require_env TELEGRAM_BOT_TOKEN
require_env MONGO_URI

railway link --project "$PROJECT_ID" --environment "$ENVIRONMENT" --service "$SERVICE" >/dev/null
set_secret TELEGRAM_BOT_TOKEN "$TELEGRAM_BOT_TOKEN"
set_secret MONGO_URI "$MONGO_URI"
set_optional_secret GEMINI_API_KEY "${GEMINI_API_KEY:-}"
set_optional_secret GROQ_API_KEY "${GROQ_API_KEY:-}"
set_optional_secret OPENSKY_CREDENTIALS_JSON "${OPENSKY_CREDENTIALS_JSON:-}"
set_optional_secret ADMIN_PASSWORD "${ADMIN_PASSWORD:-}"
set_optional_secret ADMIN_TELEGRAM_ID "${ADMIN_TELEGRAM_ID:-}"
set_secret DATABASE_NAME "aircraft_bot"

echo "Railway secret sync complete. Secret values were not printed."
