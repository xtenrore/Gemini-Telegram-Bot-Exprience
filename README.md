# Plane Alerts

**Real-time aircraft spotting alerts built around trajectory, route context, and measured prediction accuracy.**

Plane Alerts watches live ADS-B traffic around saved spotting locations and tries to answer the question that matters: **is this aircraft actually going to pass close enough to photograph?**

It does not alert simply because an aircraft is nearby, pointed in roughly the right direction, or flying to a nearby airport. The system continuously evaluates motion, projected closest approach, route behaviour, prediction confidence, and what happened after earlier predictions.

The current generation is **Plane Alerts v4.0**.

## v4.0 — Prediction Lab

v4.0 adds a continuous prediction-audit system around the existing spotting engine.

Plane Alerts now records what it expected to happen and compares that with what actually happened later. The Prediction Lab focuses on the parts of an aircraft alert system that are easiest to get subtly wrong:

- ETA accuracy and stability
- projected closest point of approach
- false or late alert cancellations
- route-history mistakes
- alert qualify/cancel oscillation
- missed close passes
- Next 60 Minutes expectations versus observed routes

A separate Railway worker runs the Antigravity investigation loop. It receives a redacted production snapshot, investigates prediction failures, and writes findings into a durable handoff queue for independent verification before any production change is accepted.

AGY findings are **hypotheses, not automatic fixes**. A finding must be checked against current telemetry and source code, reproduced where possible, and protected by regression or replay tests before a change is promoted.

### No paid runtime AI credits

The aircraft prediction engine does not depend on paid AI APIs.

The Antigravity worker uses the authenticated Google AI Pro account configuration with paid-credit overages disabled. It does not fall back to a paid Gemini API key, Claude, GPT, or another paid model when account quota is exhausted.

If AI is unavailable, the aircraft alert system continues working because trajectory, CPA, ETA, pass qualification, cancellation timing, route checks, sun position, framing, and other physical decisions are performed by deterministic or statistical code.

## How an alert is decided

For each relevant aircraft, Plane Alerts keeps a short bounded trajectory history and evaluates:

- current horizontal and slant distance
- whether distance is decreasing or increasing
- heading and groundspeed consistency
- recent turn rate and acceleration
- vertical behaviour
- projected path
- horizontal and slant CPA
- time to CPA
- ADS-B position age and update quality
- prediction confidence
- recent route behaviour for the same flight number

The alert engine can classify an aircraft as states such as **Approaching**, **Passing nearby**, **Moving away**, **Will not approach**, **Prediction uncertain**, **Turning away**, **Trajectory changed**, and **Passed**.

An active alert is continuously recalculated. If the evidence changes, the message can be updated or cancelled instead of continuing a countdown that no longer makes sense.

## Flight-number route history

Historical routing is keyed by the **flight number / transmitted flight callsign**, not by aircraft registration.

That matters because the same scheduled flight can be operated by different airframes while still following a recognisable route pattern. Plane Alerts stores bounded recent route traces and can compare today's developing path with recent flights while keeping live geometry authoritative.

Route history is used as supporting evidence, not as permission for an old route to blindly override what the aircraft is doing now.

## Next 60 Minutes

v4.0 also introduces a shadow-learning path for longer-range spotting expectations.

The system can build an expectation from recent flight-number route timing and later compare it with the route actually observed today. Longer horizons are deliberately treated with more uncertainty than live CPA prediction.

Missing ADS-B coverage is recorded as **unresolved coverage** rather than being counted as either a correct forecast or a miss.

The 30–60 minute system remains measurement-first: it should earn trust from recorded outcomes before it is allowed to behave like a precise live ETA system.

## Shared ADS-B polling

Nearby users share regional aircraft snapshots instead of each user independently hitting the same ADS-B providers.

The polling layer:

- groups compatible users into shared geographic regions
- rotates public ADS-B sources
- keeps OpenSky as a fallback rather than querying it on every cycle
- reuses recent snapshots during brief upstream gaps
- keeps active regions on a faster cadence
- slows quiet discovery regions
- bounds aircraft history in memory

This keeps the multi-user architecture efficient without sacrificing the fast updates needed for close approaches.

## Photography intelligence

Plane Alerts includes a photography layer designed for aircraft spotting rather than generic camera advice.

Depending on available data and the saved camera/lens profile, it can estimate or recommend:

- shutter speed
- aperture
- Auto ISO limits
- focal length / framing range
- aircraft frame fill
- clipping risk
- angular motion
- lighting direction
- sun position
- atmospheric clarity
- heat haze
- upper-air conditions
- contrail formation / persistence
- useful shooting-window timing

The physical calculations remain deterministic. AI may explain a result, but it is not the authority deciding whether an aircraft passes the observer.

## Telegram

Plane Alerts is controlled primarily through Telegram.

| Command | Purpose |
| --- | --- |
| `/start` | Set up Plane Alerts |
| `/status` | Show the current monitoring setup |
| `/location` | Set the spotting location |
| `/preferences` | Configure aircraft and alert preferences |
| `/camera` | Set the camera body |
| `/lens` | Set the lens |
| `/photo` | Show current shooting guidance |
| `/conditions` | Show weather, sun and atmospheric information |
| `/spotting` | Open spotting tools |
| `/help` | Show command help |

The private AGY console is restricted separately and is not intended to expose a general-purpose host shell.

## Production architecture

Production runs on Railway and uses MongoDB for persistent application and Prediction Lab state.

The main application contains the FastAPI service, Telegram bot, shared ADS-B monitor, trajectory engine, route-history system, photography intelligence, and admin tooling.

A separate **Plane-Alerts-AGY** Railway worker handles the Antigravity investigation loop and Prediction Lab bridge. Its production context is redacted before AGY receives it; database credentials and exact observer coordinates remain on the parent side.

New AGY findings are persisted immediately and emitted as `CHATGPT_HANDOFF_JSON` records so an independent engineering task can process them later without needing to run at the same minute as AGY.

## Reliability model

The v4.0 improvement loop is intentionally evidence-driven:

```text
Live ADS-B
   ↓
Production predictor
   ↓
Prediction snapshots
   ↓
Actual observed outcome
   ↓
Prediction Lab
   ↓
AGY investigation
   ↓
Durable finding handoff
   ↓
Independent verification
   ↓
Regression / replay tests
   ↓
Safer candidate change
   ↓
CI + deployment
```

Serious prediction failures should become permanent regression cases so the same bug cannot quietly return later.

## Running locally

Plane Alerts uses Python 3.11 and MongoDB.

```bash
git clone https://github.com/xtenrore/Gemini-Telegram-Bot-Exprience.git
cd Gemini-Telegram-Bot-Exprience

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env

uvicorn app.main:app --host 0.0.0.0 --port 8000
```

The practical minimum is a Telegram bot token and MongoDB connection. ADS-B provider endpoints have defaults in the example environment file.

| Variable | Used for |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | Telegram bot runtime |
| `MONGO_URI` | MongoDB connection |
| `DATABASE_NAME` | Database name |
| `WEBHOOK_URL` | Optional Telegram webhook mode |
| `WEBHOOK_SECRET` | Optional webhook verification |
| `OPENSKY_1` ... `OPENSKY_5` | Optional OpenSky credentials |
| `POLL_INTERVAL_SECONDS` | Base monitor interval |
| `DEFAULT_RADIUS_KM` | Default spotting radius |

See [`.env.example`](.env.example) for the full configuration.

## Project layout

```text
app/
  aircraft/       ADS-B providers and aircraft normalization
  bot/            Telegram commands, callbacks and messages
  intelligence/   trajectory, route history, camera and environment logic
  photography/    spotting conditions and camera guidance
  worker/         shared polling, matching and reliability systems
  admin/          admin API and dashboard

scripts/           Railway, AGY and verification helpers
tests/             trajectory, replay, alert, provider and runtime tests
docs/              focused technical documentation
.github/workflows/ CI and deployment workflows
```

## Design principle

> **Deterministic code decides what is physically happening. AI only explains, investigates, or proposes improvements.**

Runtime AI must never be the component that decides trajectory, CPA, ETA, pass/no-pass, or alert timing.

## Data limitations

ADS-B data is observational. Upstream feeds can be delayed, incomplete, duplicated, stale, or temporarily wrong, and receiver coverage varies by region.

Plane Alerts reduces those problems with stale-data rejection, bounded motion history, multiple providers, shared polling, confidence checks, route context, continuous outcome measurement, and replay/regression testing. It still avoids claiming certainty when the underlying evidence is incomplete.
