#!/usr/bin/env bash
# ============================================================================
# Oracle Cloud VM Setup Script — Aircraft Alert Telegram Bot (Polling Mode)
# ============================================================================
# Run as root (sudo bash setup.sh) on a fresh Ubuntu 22.04 instance.
#
# This script installs:
#   - Python 3.11
#   - MongoDB 7.0
#   - Bot application (venv + dependencies)
#   - Systemd services
# ============================================================================

set -euo pipefail

APP_DIR="/opt/aircraft-bot"
APP_USER="ubuntu"

echo "============================================"
echo "  Aircraft Alert Bot — Server Setup"
echo "============================================"

# ── 1. System updates ────────────────────────────────────────────────────
echo ""
echo "[1/5] Updating system packages …"
apt update && apt upgrade -y

# ── 2. Python 3.11 ───────────────────────────────────────────────────────
echo ""
echo "[2/5] Installing Python 3.11 …"
apt install -y software-properties-common
add-apt-repository -y ppa:deadsnakes/ppa
apt update
apt install -y python3.11 python3.11-venv python3.11-dev python3-pip

# ── 3. MongoDB 7.0 ──────────────────────────────────────────────────────
echo ""
echo "[3/5] Installing MongoDB 7.0 …"
apt install -y gnupg curl
curl -fsSL https://www.mongodb.org/static/pgp/server-7.0.asc | \
    gpg --dearmor -o /usr/share/keyrings/mongodb-server-7.0.gpg
echo "deb [ arch=amd64,arm64 signed-by=/usr/share/keyrings/mongodb-server-7.0.gpg ] https://repo.mongodb.org/apt/ubuntu jammy/mongodb-org/7.0 multiverse" | \
    tee /etc/apt/sources.list.d/mongodb-org-7.0.list
apt update
apt install -y mongodb-org
systemctl start mongod
systemctl enable mongod
echo "MongoDB status:"
systemctl is-active mongod

# ── 4. Application setup ────────────────────────────────────────────────
echo ""
echo "[4/5] Setting up application …"
mkdir -p "$APP_DIR"

echo "  → Copy your project files to $APP_DIR now."
echo "  → Then run the remaining steps, or continue if files are already there."

# Create venv and install dependencies (if requirements.txt exists)
if [ -f "$APP_DIR/requirements.txt" ]; then
    cd "$APP_DIR"
    python3.11 -m venv venv
    source venv/bin/activate
    pip install --upgrade pip
    pip install -r requirements.txt
    deactivate
    chown -R "$APP_USER:$APP_USER" "$APP_DIR"
    echo "  ✓ Python virtual environment created and dependencies installed."
else
    echo "  ⚠ requirements.txt not found in $APP_DIR — skipping venv setup."
    echo "    Copy your project files and run:"
    echo "    cd $APP_DIR && python3.11 -m venv venv && source venv/bin/activate && pip install -r requirements.txt"
fi

# ── 5. Install systemd services ─────────────────────────────────────────
echo ""
echo "[5/5] Installing systemd services …"
if [ -f "$APP_DIR/deploy/aircraft-bot.service" ]; then
    cp "$APP_DIR/deploy/aircraft-bot.service" /etc/systemd/system/
    cp "$APP_DIR/deploy/aircraft-worker.service" /etc/systemd/system/
    systemctl daemon-reload
    systemctl enable aircraft-bot aircraft-worker
    echo "  ✓ Services installed and enabled."
    echo "  → Start with: sudo systemctl start aircraft-bot aircraft-worker"
else
    echo "  ⚠ Service files not found — install manually."
fi

echo ""
echo "============================================"
echo "  Setup complete!"
echo "============================================"
echo ""
echo "Next steps:"
echo "  1. Copy your project files to $APP_DIR (if you haven't already)"
echo "  2. Create $APP_DIR/.env (use .env.example as template) and add your bot token"
echo "  3. Start the bot and worker:"
echo "       sudo systemctl start aircraft-bot aircraft-worker"
echo ""
echo "Monitor with:"
echo "  journalctl -u aircraft-bot -f"
echo "  journalctl -u aircraft-worker -f"
