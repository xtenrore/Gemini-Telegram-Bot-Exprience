#!/usr/bin/env bash
set -euo pipefail

STATE_DIR="${AGY_STATE_DIR:-/agy-state}"
export HOME="$STATE_DIR/home"
export XDG_DATA_HOME="$HOME/.local/share"
export XDG_CONFIG_HOME="$HOME/.config"
export XDG_CACHE_HOME="$HOME/.cache"

mkdir -p \
  "$HOME/.gemini/antigravity-cli" \
  "$XDG_DATA_HOME/keyrings" \
  "$XDG_CONFIG_HOME" \
  "$XDG_CACHE_HOME" \
  "$STATE_DIR/prediction-lab/context"

if [[ -z "${AGY_KEYRING_PASSWORD:-}" ]]; then
  echo "FATAL: AGY_KEYRING_PASSWORD is required so the persistent Linux Secret Service can be unlocked after a Railway restart." >&2
  exit 78
fi

# Antigravity account sessions are stored through Linux Secret Service. Start
# one D-Bus session for the lifetime of this container, then unlock/create the
# login keyring using a Railway secret. The encrypted keyring files themselves
# live under the persistent volume via XDG_DATA_HOME.
eval "$(dbus-launch --sh-syntax)"
KEYRING_ENV="$(printf '%s' "$AGY_KEYRING_PASSWORD" | gnome-keyring-daemon --unlock --components=secrets 2>/tmp/agy-keyring-error.log || true)"
if [[ -n "$KEYRING_ENV" ]]; then
  eval "$KEYRING_ENV"
fi

if ! secret-tool store --label='Plane Alerts keyring probe' plane-alerts probe <<<"ok" >/dev/null 2>&1; then
  echo "FATAL: Linux Secret Service is not usable; refusing to start because AGY authentication would not reliably survive restarts." >&2
  cat /tmp/agy-keyring-error.log >&2 || true
  exit 78
fi
if [[ "$(secret-tool lookup plane-alerts probe 2>/dev/null || true)" != "ok" ]]; then
  echo "FATAL: persistent keyring probe failed." >&2
  exit 78
fi

# Never let an inherited API key silently switch this worker onto billable API
# usage. Account-based Google AI Pro authentication is the only allowed path.
unset GEMINI_API_KEY GOOGLE_API_KEY GOOGLE_GEMINI_API_KEY GOOGLE_GEMINI_BASE_URL || true

export PATH="/usr/local/bin:$PATH"
export PYTHONPATH="/app${PYTHONPATH:+:$PYTHONPATH}"
export AGY_CLI_DISABLE_AUTO_UPDATE=true

# Seed first-launch choices directly on the persistent volume so a phone-only
# user does not have to navigate AGY's theme/rendering/workspace-trust wizard.
# Also arm the persisted supervisor when AGY_GOAL_ENABLED=true. Importantly we
# preserve a future next_run_at so a Railway restart cannot bypass a quota wait.
python - <<'PY'
import json, os
from pathlib import Path

home = Path(os.environ['HOME'])
settings_path = home / '.gemini' / 'antigravity-cli' / 'settings.json'
settings_path.parent.mkdir(parents=True, exist_ok=True)
try:
    data = json.loads(settings_path.read_text())
except Exception:
    data = {}
data.pop('modelProvider', None)
data['useG1Credits'] = False
data['colorScheme'] = data.get('colorScheme') or 'terminal'
data['altScreenMode'] = 'never'
data['agentMode'] = 'accept-edits'
data['enableTelemetry'] = False
trusted = list(data.get('trustedWorkspaces') or [])
if '/app' not in trusted:
    trusted.append('/app')
data['trustedWorkspaces'] = trusted
tmp = settings_path.with_suffix('.tmp')
tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
tmp.replace(settings_path)

state_dir = Path(os.environ.get('AGY_STATE_DIR', '/agy-state'))
supervisor_path = state_dir / 'prediction-lab' / 'supervisor.json'
supervisor_path.parent.mkdir(parents=True, exist_ok=True)
try:
    supervisor = json.loads(supervisor_path.read_text())
except Exception:
    supervisor = {}
enable = os.environ.get('AGY_GOAL_ENABLED', '').strip().lower() in {'1', 'true', 'yes', 'on'}
if enable:
    was_enabled = bool(supervisor.get('enabled', False))
    supervisor['enabled'] = True
    goal = os.environ.get('AGY_GOAL', '').strip()
    if goal:
        supervisor['goal'] = goal
    if not was_enabled:
        supervisor['next_run_at'] = 0
    stmp = supervisor_path.with_suffix('.tmp')
    stmp.write_text(json.dumps(supervisor, indent=2, sort_keys=True))
    stmp.replace(supervisor_path)
PY

# Parent-side bridge keeps Mongo credentials. AGY itself never receives them.
# It refreshes redacted truth every 30s and publishes findings every 3s.
python /app/scripts/agy_bridge_daemon.py &
BRIDGE_PID=$!
echo "Prediction Lab bridge started pid=$BRIDGE_PID"

exec uvicorn app.agy_worker_ext:app --host 0.0.0.0 --port "${PORT:-8090}" --workers 1
