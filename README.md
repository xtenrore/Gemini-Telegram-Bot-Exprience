# ✈️ Aircraft Alert Telegram Bot (Single-VM Linux Edition)

A high-performance Telegram bot and monitoring system that monitors ADS-B aircraft data in real-time and alerts users when aircraft types of interest pass near their location or enter a projected collision/intercept trajectory.

Consolidated into a **single unified Linux VM deployment** with automated systemd background services.

---

## 🌟 Key Features

- **Single-VM Architecture** — Both the FastAPI web server / Telegram bot and the 5-second background monitoring worker run on the same VM with zero cross-VM latency.
- **Automated Linux Setup Script (`setup.sh`)** — Automatically installs system dependencies, Python 3, MongoDB 7.0, creates venv, sets up `.env`, registers systemd services, and launches them in the background.
- **Service Management (`manage.sh`)** — Easy commands to start, stop, restart, view live logs, and run automated tests.
- **Flexible Bot Operation (Polling or Webhook)**:
  - **Long Polling (Default)**: Runs without requiring a public domain, SSL certificates, or open incoming ports.
  - **HTTPS Webhook (Optional)**: Automatically activates if `WEBHOOK_URL` is set in `.env`.
- **Web Admin Dashboard (`/admin`)** — Real-time metrics for active users, notifications sent, provider health, memory usage, and background worker cycle status.
- **Multi-Source ADS-B Data Feeds (Parallel Query + Failover)**:
  - [ADSB.lol](https://www.adsb.lol/) — High-speed community feed with ICAO aircraft types
  - [ADSB.fi](https://www.adsb.fi/) — High-availability European feed
  - [Airplanes.Live](https://airplanes.live/) — Global aggregated feed
  - [ADSB.one](https://adsb.one/) — Low-latency community feed
  - [OpenSky Network](https://opensky-network.org/) — Multi-key rotation backup
- **Native 3D Kinematics & Trajectory Forecasting** — In-process physics engine calculating turn rates, Closest Distance of Approach (CDA), and ETA over an extended +15km outer early-warning buffer.
- **Smart Anti-Spam** — 30-minute configurable cooldown per aircraft per user.
- **Category & Custom Filtering** — Military, Large Airliners, Cargo, Business Jets, Helicopters, Government, VIP, and custom ICAO type codes (e.g. `B738`, `A21N`, `C17`).

---

## 🏗️ Architecture

```
                               ┌────────────────────────────────────────────────────────┐
                               │                    Single Linux VM                     │
                               │                                                        │
Telegram Servers               │   ┌────────────────────────────────────────────────┐   │
   │                           │   │  aircraft-bot.service (FastAPI + Telegram)     │   │
   ├─ (HTTPS Webhook) ─────────┼─► │    • GET  /            -> HTTP 200 OK          │   │
   │                           │   │    • GET  /health      -> Health status JSON   │   │
   └─ (or Long Polling) ◄──────┼─► │    • GET  /stats       -> System statistics    │   │
                               │   │    • POST /webhook     -> Webhook updates      │   │
                               │   │    • GET  /admin       -> Web Admin Dashboard  │   │
                               │   └────────────────────────┬───────────────────────┘   │
                               │                            │                           │
                               │                            ▼                           │
                               │                   ┌─────────────────┐                  │
                               │                   │  MongoDB 7.0    │                  │
                               │                   └────────┬────────┘                  │
                               │                            ▲                           │
ADS-B Providers                │                            │                           │
(ADSB.lol, ADSB.fi,            │   ┌────────────────────────┴───────────────────────┐   │
 Airplanes.Live, ADSB.one) ────┼─► │  aircraft-worker.service (Trajectory Monitor)  │   │
                               │   │    • 5-second polling cycle                    │   │
                               │   │    • Native kinematics & early warning         │   │
                               │   │    • Notifications via Telegram Bot API        │   │
                               │   └────────────────────────────────────────────────┘   │
                               └────────────────────────────────────────────────────────┘
```

---

## 🚀 Quick Start (Automated Linux Setup)

Run this on any Linux server (Ubuntu 24.04 / 22.04 / 20.04, Debian 12 / 11, Oracle Cloud, DigitalOcean, Hetzner, AWS, etc.):

```bash
# 1. Clone the repository
git clone https://github.com/xtenrore/Gemini-Telegram-Bot-Exprience.git
cd Gemini-Telegram-Bot-Exprience

# 2. Run the automated Linux setup script (as root or with sudo)
sudo bash setup.sh
```

The script automatically:
1. Installs system packages, Python 3, and build tools.
2. Installs and starts MongoDB Community Edition.
3. Sets up Python virtual environment (`venv/`) and dependencies.
4. Generates `.env` from `.env.example` and prompts for your Telegram Bot Token.
5. Installs `aircraft-bot.service`, `aircraft-worker.service`, and `aircraft.target`.
6. Enables and immediately starts both background services via `systemctl`.
7. Tests local endpoints and displays service health.

---

## 🛠️ Service Management

Use the included `./manage.sh` helper script:

```bash
# Check status of bot, worker, MongoDB, and health endpoint
./manage.sh status

# Restart both background services
./manage.sh restart

# Stop background services
./manage.sh stop

# Start background services
./manage.sh start

# Stream live combined logs from journalctl
./manage.sh logs

# Stream individual service logs
./manage.sh logs-bot
./manage.sh logs-worker

# Run automated test suite
./manage.sh test
```

Standard `systemctl` commands are also fully supported:
```bash
sudo systemctl status aircraft-bot aircraft-worker
sudo systemctl restart aircraft.target
```

---

## ⚙️ Configuration (`.env`)

All settings are configured in `.env`. Copy from `.env.example`:

| Variable | Default | Description |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Required | Telegram bot token from [@BotFather](https://t.me/BotFather) |
| `WEBHOOK_URL` | *blank* | Public URL for webhooks. **Leave empty to use Long Polling** |
| `WEBHOOK_SECRET` | *blank* | Optional secret token for Telegram webhook validation |
| `MONGO_URI` | `mongodb://localhost:27017` | Local or remote MongoDB connection URI |
| `DATABASE_NAME` | `aircraft_bot` | MongoDB database name |
| `POLL_INTERVAL_SECONDS` | `5` | ADS-B query interval in seconds |
| `DEFAULT_RADIUS_KM` | `15.0` | Default user detection radius in km |
| `COOLDOWN_MINUTES` | `30` | Minutes to suppress repeat alerts for same aircraft |
| `ADMIN_PASSWORD` | *blank* | Optional password protecting `/admin` dashboard |
| `ADMIN_TELEGRAM_ID` | *blank* | Optional Telegram ID for admin notifications |
| `GEMINI_API_KEY` | *blank* | Optional Gemini API key for AI judge and conflict resolution |
| `GROQ_API_KEY` | *blank* | Optional Groq API key for AI verification cascade |

---

## 📊 HTTP Endpoints & Admin Dashboard

The FastAPI web service exposes the following endpoints (default port `8000`):

- **`GET /`** — Plain text `"OK"` for deployment health checks and UptimeRobot.
- **`GET /health`** — JSON status checking MongoDB connectivity, Telegram bot mode, and worker cycle statistics.
- **`GET /stats`** — JSON overview of active users, total users, and polling configuration.
- **`GET /admin`** — Interactive Admin Dashboard with live stats, provider metrics, and user management.
- **`GET /admin/api/*`** — Dashboard JSON APIs (`/overview`, `/users`, `/providers`, `/keys`, `/notifications`, `/system`).
- **`POST /webhook`** — Telegram update receiver (active when `WEBHOOK_URL` is set).

---

## 🤖 Telegram Bot Commands

| Command | Description |
|---|---|
| `/start` | Welcome message, disclaimer acceptance, and initial setup |
| `/setup` | Reset preferences and run full setup flow again |
| `/status` | View current location, radius, and watched aircraft types |
| `/location` | Update your monitoring GPS coordinates and radius |
| `/preferences` | Change watched aircraft categories and custom ICAO codes |
| `/help` | Show command reference and usage help |
| `/cancel` | Cancel current interactive input step |

---

## 📁 Project Structure

```
.
├── app/
│   ├── admin/                 # Admin dashboard routes & static frontend
│   │   ├── static/            # HTML5 dashboard, CSS styling, and JavaScript logic
│   │   └── routes.py          # Dashboard API routes (/admin/api/*)
│   ├── aircraft/              # Aircraft providers, models, AI judge, & learning
│   │   ├── api_keys.py        # OpenSky OAuth2 token management & key rotation
│   │   ├── ai_judge.py        # Gemini + Groq AI cascade verification
│   │   ├── categories.py      # Aircraft type categories and prefix matchers
│   │   ├── learner.py         # Per-user provider selection & observation engine
│   │   ├── models.py          # NormalizedAircraft unified schema
│   │   └── providers.py       # ADS-B multi-provider fetchers (parallel query)
│   ├── bot/                   # Telegram bot UI & interactions
│   │   ├── feedback.py        # Like/dislike notification feedback handlers
│   │   ├── handlers.py        # Command and callback query handlers
│   │   ├── keyboards.py       # Inline keyboard builders
│   │   ├── messages.py        # HTML message templates
│   │   └── states.py          # FSM conversation states
│   ├── worker/                # Monitoring & trajectory engine
│   │   ├── geo.py             # Haversine distance, bounding boxes, geohash
│   │   ├── kinematics.py      # Trajectory simulation, curve projection, CDA & ETA
│   │   ├── monitor.py         # Main ADS-B polling and matching loop
│   │   └── notifications.py   # Telegram notification sender with rate limiter
│   ├── config.py              # Application settings (Pydantic BaseSettings)
│   ├── database.py            # Motor MongoDB async client & index management
│   └── main.py                # FastAPI web server, admin dashboard, & bot runner
├── deploy/
│   ├── aircraft-bot.service   # Systemd unit template for web/bot service
│   ├── aircraft-worker.service# Systemd unit template for background monitor
│   ├── aircraft.target        # Systemd target unit for managing both services
│   ├── Caddyfile              # Optional reverse proxy configuration
│   └── setup.sh               # Deployment setup wrapper
├── api/                       # OpenSky API key credentials
├── manage.sh                  # Management CLI (status, start, stop, restart, logs, test)
├── setup.sh                   # Automated single-VM Linux installer
├── worker.py                  # Standalone background worker runner
├── requirements.txt           # Python dependencies
├── .env.example               # Environment template
└── Procfile                   # Process definition
```

---

## 🧪 Testing

Run the automated test suite:

```bash
./manage.sh test
# or
./venv/bin/pytest -v
```

---

## 📄 License

MIT License.
