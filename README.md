# Plane Alerts

**Real-time aircraft spotting alerts with trajectory-aware filtering.**

Plane Alerts is a Telegram-based aircraft spotting system built around one simple rule: **an aircraft being nearby is not enough to alert**. It follows live ADS-B motion, estimates the aircraft's closest point of approach to a saved location, and only sends an approach alert when the projected pass actually makes sense.

The current production version is **Plane Alerts v3.5**. It runs on Railway as a single service containing the FastAPI API, Telegram bot, and background aircraft monitor.

## What Plane Alerts does

- Tracks live aircraft around saved user locations using several ADS-B sources, with provider rotation and fallback when a feed is unavailable.
- Uses deterministic trajectory calculations for distance, approach state, closest point of approach, time to closest approach, turn behavior, and prediction confidence.
- Keeps short, bounded trajectory history so a single noisy ADS-B sample does not create a false alert.
- Uses recent flight-number route history as a second veto layer. For supported flights, Plane? can compare today's path with the previous three UTC days and suppress alerts when the normal route turns away or reaches its destination before the projected pass.
- Shares ADS-B polling between nearby users instead of making a separate provider request for every user. Quiet regions are polled less often; active regions automatically move to a faster update cycle.
- Supports aircraft-category filters, custom ICAO type codes, and an all-aircraft monitoring mode.
- Includes a spotting and photography system with camera/lens profiles, sun position, weather, haze, upper-air conditions, framing estimates, shutter guidance, and contrail-related information.
- Exposes health, statistics, and admin endpoints for deployment monitoring.

## Alert logic

Plane? does not treat current distance, destination airport, or heading alone as proof that an aircraft is coming toward the observer. The monitor builds recent motion history, projects the path, calculates the closest pass, checks whether the prediction is stable enough to trust, and then applies route-history checks when useful.

If an aircraft turns away or the prediction changes enough that the pass no longer qualifies, the active approach state is cancelled instead of continuing to count down to a pass that will never happen.

The core trajectory and photography calculations are deterministic. Gemini is optional and is used only for explanation or enhancement; the alert engine does not depend on it to decide basic geometry, ETA, sun position, camera fundamentals, or whether a pass qualifies.

## v3.5 shared polling

Version 3.5 changed how ADS-B traffic is fetched. Nearby users are grouped into safe shared query regions and reuse the same aircraft snapshot. Public providers are rotated instead of all being queried on every cycle, OpenSky is kept as a fallback, and cached snapshots are briefly reused when a feed returns a transient empty result.

The main scheduler still runs every five seconds, but provider traffic is adaptive: active regions can refresh every five seconds while quiet discovery regions use a slower interval. This keeps alert latency low without making API usage grow linearly with the number of users.

## Telegram commands

| Command | Purpose |
| --- | --- |
| `/start` | Set up Plane? and aircraft alerts |
| `/status` | Show the current monitoring setup |
| `/location` | Set the monitoring / shooting location |
| `/preferences` | Choose aircraft types and alert preferences |
| `/camera` | Set the camera body |
| `/lens` | Set the aircraft lens |
| `/photo` | Get current shooting guidance |
| `/conditions` | Show weather, sun and atmospheric conditions |
| `/spotting` | Open Spotting Mode |
| `/help` | Show command help |

## Running locally

Plane? uses Python 3.11 and MongoDB. Copy the example environment file first and fill in the services you want to use.

```bash
git clone https://github.com/xtenrore/Gemini-Telegram-Bot-Exprience.git
cd Gemini-Telegram-Bot-Exprience

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env

uvicorn app.main:app --host 0.0.0.0 --port 8000
```

The minimum practical setup is a Telegram bot token and a MongoDB connection. ADS-B provider URLs already have defaults in `.env.example`. Gemini and Groq are optional enhancement/fallback integrations.

| Variable | Used for |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | Telegram bot runtime |
| `MONGO_URI` | MongoDB / MongoDB Atlas connection |
| `DATABASE_NAME` | Database name |
| `WEBHOOK_URL` | Optional Telegram webhook mode |
| `WEBHOOK_SECRET` | Optional webhook verification |
| `GEMINI_API_KEY` | Optional AI enhancement |
| `OPENSKY_1` ... `OPENSKY_5` | Optional OpenSky OAuth credentials |
| `POLL_INTERVAL_SECONDS` | Base monitor loop interval |
| `DEFAULT_RADIUS_KM` | Default user alert radius |

See [`.env.example`](.env.example) for the full configuration.

## Production deployment

Production is containerized with the root [`Dockerfile`](Dockerfile) and deployed to Railway. The container starts through [`scripts/railway-entrypoint.sh`](scripts/railway-entrypoint.sh), which verifies the production integrations before launching the service.

Changes on `main` are tested by GitHub Actions. A successful test workflow can then trigger the Railway deployment workflow so the exact tested commit is what gets deployed.

The Railway production process uses one Python runtime for FastAPI, Telegram, and the integrated ADS-B monitor. The older VM/systemd deployment files remain in the repository for self-hosted setups, but they are not the primary production path.

## HTTP endpoints

| Endpoint | Purpose |
| --- | --- |
| `/` | Lightweight deployment health check |
| `/health` | Database, bot and worker health information |
| `/stats` | User and shared-polling statistics |
| `/admin` | Admin dashboard |
| `/webhook` | Telegram webhook receiver when webhook mode is enabled |

## Project layout

```text
app/
  aircraft/       ADS-B providers, normalization and provider intelligence
  bot/            Telegram commands, callbacks and messages
  intelligence/   trajectory, route-history, camera and environment logic
  photography/    photography conditions and Telegram spotting tools
  worker/         monitoring, matching, reliability and v3.5 shared polling
  admin/          admin API and dashboard

.github/workflows/ CI, Railway deploy and secret-sync workflows
docs/              focused technical notes
scripts/           Railway/runtime verification helpers
tests/             trajectory, alert, provider, photography and runtime tests
```

## Design principle

**Deterministic code decides what is physically happening. AI only explains or enhances the result.**

That means a Gemini outage, timeout, or quota limit should not stop Plane? from deciding whether an aircraft is approaching, calculating the pass geometry, or producing usable spotting guidance.

## Notes

ADS-B data can be delayed, incomplete, duplicated, or briefly incorrect depending on the upstream feed and receiver coverage. Plane? uses confidence checks, stale-data rejection, bounded history, multiple providers, and route validation to reduce those problems, but live aviation data should still be treated as observational rather than authoritative.

More detail on the route-history gate is available in [`docs/FLIGHT_ROUTE_HISTORY.md`](docs/FLIGHT_ROUTE_HISTORY.md). Photography-specific notes are in [`docs/V3.2_PHOTOGRAPHY.md`](docs/V3.2_PHOTOGRAPHY.md).
