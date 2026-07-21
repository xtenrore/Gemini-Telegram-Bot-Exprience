# ✈️ Aircraft Alert Telegram Bot

A Telegram bot that monitors ADS-B aircraft data in real-time and sends notifications when user-selected aircraft types pass near their location.

## Features

- **Real-time monitoring** — Polls aircraft data every 45 seconds
- **Multi-source failover** — ADSB.lol → ADSB.fi → OpenSky automatic fallback
- **Smart notifications** — 30-minute cooldown per aircraft to avoid spam
- **Category-based filtering** — Military, Large Airliners, Cargo, Business Jets, Helicopters, Government, Experimental, VIP Aircraft
- **Custom aircraft types** — Track any ICAO type designator
- **Geohash clustering** — Efficient API usage by grouping nearby users
- **Webhook architecture** — Event-driven, ideal for Oracle Cloud Free Tier

## Architecture

```
Telegram → Caddy (HTTPS) → FastAPI (webhook) → MongoDB
                                                    ↑
                        Background Worker (APScheduler)
                          ↓
                    ADSB.lol / ADSB.fi / OpenSky
```

## Quick Start (Local Development)

### Prerequisites

- Python 3.11+
- MongoDB (running locally on default port)
- Telegram Bot Token (from [@BotFather](https://t.me/BotFather))

### Setup

```bash
# Clone the repository
git clone <your-repo-url>
cd aircraft-bot

# Create virtual environment
python -m venv venv
source venv/bin/activate      # Linux/macOS
# or
venv\Scripts\activate         # Windows

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your Telegram bot token and settings
```

### Run

**Option A — Both services in one terminal (development):**

```bash
# Terminal 1: FastAPI webhook server
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# Terminal 2: Background worker
python worker.py
```

**Option B — Using ngrok for local webhook testing:**

```bash
# Terminal 1: Start ngrok
ngrok http 8000

# Copy the HTTPS URL and set WEBHOOK_URL in .env
# Then start the bot server and worker
```

## Deployment (Oracle Cloud Free Tier)

### Automated Setup

```bash
# SSH into your VM
ssh ubuntu@your-vm-ip

# Upload project files to /opt/aircraft-bot
# Then run the setup script:
sudo bash deploy/setup.sh
```

### Manual Steps

1. **Configure domain** — Point your domain/subdomain to the VM's public IP
2. **Edit Caddyfile** — Replace `YOUR_DOMAIN.com` in `/etc/caddy/Caddyfile`
3. **Create .env** — `cp .env.example .env && nano .env`
4. **Open firewall** — Allow ports 80 and 443 in Oracle Cloud Security List
5. **Start services**:
   ```bash
   sudo systemctl restart caddy
   sudo systemctl start aircraft-bot aircraft-worker
   ```

### Monitoring

```bash
# Check service status
systemctl status aircraft-bot aircraft-worker mongod caddy

# View logs
journalctl -u aircraft-bot -f
journalctl -u aircraft-worker -f

# Health check
curl https://your-domain.com/health
curl https://your-domain.com/stats
```

## Bot Commands

| Command | Description |
|---------|-------------|
| `/start` | Initial welcome & setup flow |
| `/setup` | Re-run full setup (resets config) |
| `/status` | View current monitoring config |
| `/help` | Show all available commands |
| `/location` | Update monitoring location |
| `/preferences` | Change aircraft type selection |
| `/cancel` | Cancel current operation |

## Project Structure

```
├── app/
│   ├── __init__.py
│   ├── config.py              # Settings from .env
│   ├── main.py                # FastAPI webhook server
│   ├── database.py            # MongoDB connection & indexes
│   ├── bot/
│   │   ├── handlers.py        # Command & message handlers
│   │   ├── keyboards.py       # Inline keyboard builders
│   │   ├── states.py          # FSM state management
│   │   └── messages.py        # Message templates
│   ├── aircraft/
│   │   ├── categories.py      # Aircraft type categories
│   │   ├── models.py          # NormalizedAircraft model
│   │   └── providers.py       # Data providers + failover
│   └── worker/
│       ├── monitor.py         # Main monitoring loop
│       ├── notifications.py   # Notification sender
│       └── geo.py             # Geospatial utilities
├── deploy/
│   ├── Caddyfile              # Reverse proxy config
│   ├── aircraft-bot.service   # Systemd service (bot)
│   ├── aircraft-worker.service # Systemd service (worker)
│   └── setup.sh               # Automated setup script
├── worker.py                   # Worker entry point
├── requirements.txt
├── .env.example
└── .gitignore
```

## Aircraft Data Sources

| Provider | Role | Rate Limit | Type Data |
|----------|------|------------|-----------|
| [ADSB.lol](https://www.adsb.lol/) | Primary | None currently | ✅ Yes |
| [ADSB.fi](https://www.adsb.fi/) | Fallback | 1 req/s | ✅ Yes |
| [OpenSky](https://opensky-network.org/) | Backup | 4000 credits/day | ❌ No |

## License

MIT
