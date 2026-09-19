# Plane Alerts

Plane Alerts is an aircraft-spotting alert system built around one rule: **live physical evidence decides whether an aircraft is likely to pass the observer; AI does not.**

The system combines live ADS-B positions, deterministic trajectory and CPA calculations, flight-number route history, arrival-turn logic, photography guidance, and a Prediction Lab that compares forecasts with what actually happened.

Current production release: **Plane Alerts v4.2.1**

Telegram: **[@planebotnotifierbot](https://t.me/planebotnotifierbot)**

## v4.2.1 — Prediction reliability

v4.2.1 is a reliability update built on the v4.2 terminal-arrival system.

The release focuses on failures found from real production telemetry and automated audits:

- uncertain close passes can recover when two fresh independent ADS-B positions prove the aircraft is physically inside the configured alert radius;
- a stale, duplicated, single, or missing position cannot use that recovery path;
- route-history persistence now uses a fixed bounded worker queue instead of creating one task per aircraft;
- slow route-history writes are time-bounded and may be dropped because route-history telemetry must never block live alert processing;
- Prediction Lab Next60 and Europe-sentinel Mongo reads use small cursor batches so large point-heavy route histories do not create oversized database responses;
- failed shadow scans return to their normal cadence instead of immediately retrying and increasing database pressure;
- AGY headless audits use a documented set of supported tools and Python standard-library analysis instead of assuming optional packages are installed;
- production CI deploys the same tested Git commit to both the main Railway service and the AGY worker, so the two services do not drift between releases.

The alert-critical rules remain deterministic. This release does not move trajectory, CPA, ETA, pass/no-pass, qualification, cancellation, or notification timing to an AI model.

## How alert qualification works

Plane Alerts does not alert because an aircraft is merely close, points toward the observer, or has a particular destination.

For each relevant aircraft the live engine can consider:

- latitude and longitude;
- heading and groundspeed;
- altitude and vertical rate;
- position freshness and update gaps;
- recent distance trend;
- turn rate and curvature;
- projected closest point of approach;
- time to CPA;
- recent route behavior for the same transmitted flight number;
- destination and airport-convergence evidence when available;
- multiple deterministic future-path hypotheses around terminal arrivals.

A terminal-arrival aircraft can temporarily appear to be heading directly toward an observer even though its normal arrival path will turn away. Plane Alerts therefore does not treat a single straight-line projection as sufficient evidence.

At the same time, route history is not allowed to hide a genuine close pass. Fresh physical observations inside the configured radius can override a weak or stale model prediction when the required evidence is present.

## Flight-number route history

Historical routes are keyed by the **flight number / transmitted callsign**, not by aircraft registration.

For example, recurring behavior for a flight such as `THY1017` is more useful than whichever aircraft registration operates that flight on a particular day.

Route history is supporting evidence. It can help identify repeated turns and corridors, but live motion remains authoritative when current observations clearly disagree with history.

Route storage is bounded. v4.2.1 also keeps route persistence away from the critical alert loop through a fixed worker queue and write timeouts.

## Prediction Lab

Prediction Lab records forecasts and later compares them with observed outcomes.

It is used to investigate:

- ETA accuracy and stability;
- projected versus observed CPA;
- missed close passes;
- false or late cancellations;
- qualify/cancel oscillation;
- route-history mistakes;
- terminal-arrival turn behavior;
- Next60 timing quality;
- regressions introduced by new releases.

Missing ADS-B coverage is not counted as a correct prediction and is not counted as a miss. It is recorded as unresolved coverage.

### Next 60 Minutes

`/next60` and `/forecast` show expected aircraft in 0–15, 15–30, and 30–60 minute windows.

Short-range live geometry remains the authority. Longer-range historical forecasts are shadow-style estimates and are deliberately treated more cautiously. Low-quality single-day or highly variable historical timing is rejected from scoring.

v4.2.1 keeps the same forecast logic while changing how the private audit worker reads large Mongo route collections: documents are returned in small batches so one large network response cannot stall the entire audit loop.

### Europe Sentinel Network

Prediction Lab also uses shadow-only European sentinel locations to collect independent route outcomes and adversarial turn-away examples. Sentinel results do not generate user alerts.

## AGY investigation worker

Plane Alerts runs a separate **Plane-Alerts-AGY** Railway service for post-outcome investigation.

The workflow is:

```text
Live ADS-B
  -> deterministic production predictor
  -> recorded expectation
  -> observed outcome
  -> Prediction Lab
  -> AGY investigation
  -> CHATGPT_HANDOFF_JSON
  -> independent verification
  -> regression/replay test
  -> candidate fix
```

AGY findings are hypotheses. They are expected to be checked against production telemetry and the current source before code is changed.

The AGY worker does not receive authority to decide live trajectory, CPA, ETA, pass/no-pass, alert qualification, cancellation, or notification timing.

## Photography intelligence

Plane Alerts also contains deterministic spotting guidance. Depending on available data and the saved camera/lens profile, it can estimate or recommend:

- shutter speed;
- aperture;
- Auto ISO limits;
- focal length;
- frame fill and clipping risk;
- angular motion;
- sun position and lighting direction;
- atmospheric clarity and haze;
- upper-air conditions;
- contrail formation and persistence;
- useful shooting-window timing.

The photography layer remains usable if AI services are unavailable.

## Telegram commands

| Command | Purpose |
| --- | --- |
| `/start` | Initial setup |
| `/status` | Monitoring status |
| `/next60` | Aircraft expected in the next 60 minutes |
| `/forecast` | Alias for `/next60` |
| `/location` | Set spotting location |
| `/preferences` | Configure aircraft and alert preferences |
| `/camera` | Configure camera body |
| `/lens` | Configure lens |
| `/photo` | Current shooting guidance |
| `/conditions` | Weather, sun and atmospheric information |
| `/spotting` | Spotting tools |
| `/help` | Command help |

The private `/agy` console is owner-only and is not a general-purpose server shell.

## Architecture

Production runs on Railway with MongoDB persistence.

Main service:

- FastAPI health/admin endpoints;
- Telegram bot;
- shared ADS-B polling;
- deterministic trajectory and CPA engine;
- terminal-arrival qualification;
- route-history system;
- photography intelligence;
- Next60 command;
- low-rate Europe sentinel sampler.

AGY service:

- redacted Prediction Lab context;
- account-based investigation loop;
- watchdog/supervisor;
- durable state on a persistent Railway volume;
- no paid-credit overage path;
- deterministic shadow audit helpers.

After main-branch CI succeeds, the deployment workflow uploads that exact tested commit to both Railway services. This keeps AGY on the same verified source as the production predictor without replacing its persistent authentication volume.

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

## Repository layout

```text
app/
  aircraft/          ADS-B providers and normalization
  bot/               Telegram commands and messages
  intelligence/      trajectory, route history and arrival logic
  photography/       deterministic camera guidance
  worker/            shared production polling
  next_hour_shadow.py
  sentinel_shadow.py
  agy_prediction_bridge.py

scripts/             deployment, audit and benchmark helpers
tests/               unit, regression and replay tests
docs/error_museum/   preserved production failure cases
.github/workflows/   CI workflows
```

## Design principle

> **Deterministic code decides what is physically happening. AI may investigate or explain the evidence, but it does not control the live alert decision.**

ADS-B data can be stale, delayed, duplicated, incomplete, or temporarily unavailable. Plane Alerts therefore records uncertainty explicitly and treats missing coverage as missing evidence rather than inventing certainty.
