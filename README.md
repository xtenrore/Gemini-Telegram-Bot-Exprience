# Plane Alerts

Plane Alerts is an aircraft-spotting alert system designed to predict whether an aircraft is genuinely likely to pass the observer, rather than alerting from proximity alone.

Live ADS-B motion, deterministic trajectory geometry, CPA/ETA calculations, flight-number route history, terminal-arrival logic, and prediction confidence drive the alert decision. AI is limited to investigation and explanation; it does not control live trajectory, CPA, ETA, pass/no-pass, qualification, cancellation, or notification timing.

Current production release: **Plane Alerts v4.2.2**

Telegram: **[@planebotnotifierbot](https://t.me/planebotnotifierbot)**

## v4.2.2 — AGY command resilience

v4.2.2 hardens the autonomous Prediction Lab investigation loop after production verification found that a headless audit could choose a forbidden compound shell command and then be incorrectly treated as a successful completed cycle.

Changes:

- permission-denied AGY runs are detected even when the CLI exits with status 0;
- denied cycles retry with bounded 15/30/60-second backoff instead of sleeping for the normal hourly goal interval;
- strict permissions remain in place; no broad shell bypass is enabled;
- multi-line analysis must use `write_to_file`, followed by a separate allowlisted `python3` command;
- `cat` heredocs, redirection, pipes, `sed`, `jq`, `sh`, `bash`, and compound shell commands remain disallowed;
- a tooling-policy version forces one immediate recovery cycle after this update so a previously denied run is not left waiting for its old hourly deadline;
- long Prediction Lab shadow scans run through one bounded background worker so context refreshes and ChatGPT handoffs continue while Mongo route analysis is still running;
- the CI-approved Git commit is deployed to both Railway services so the production bot and AGY worker run the same tested source.

No paid AI/API dependency is introduced.

## v4.2.1 — Prediction reliability

v4.2.1 added several reliability fixes based on real production outcomes:

- two fresh independent ADS-B positions can recover an uncertain close-pass candidate when the aircraft is physically inside the configured alert radius;
- stale, duplicated, single, or missing positions cannot use that recovery path;
- route-history persistence uses a fixed bounded worker queue rather than one task per aircraft;
- slow route-history writes are time-bounded and may be dropped because telemetry must not block live alert processing;
- Prediction Lab Mongo scans use bounded cursor batches and dedicated indexes;
- failed shadow scans return to their normal cadence rather than entering a retry storm;
- low-quality Next60 historical expectations remain excluded from scoring;
- missing ADS-B coverage remains unresolved rather than being counted as a success or miss.

## Alert qualification

Plane Alerts does not alert simply because an aircraft is nearby, points toward the observer, or is assigned to a particular destination.

The deterministic live engine can consider:

- current latitude and longitude;
- heading and groundspeed;
- altitude and vertical rate;
- position age and update gaps;
- recent distance trend;
- turn rate and curvature;
- projected closest point of approach;
- time to CPA;
- recent route behavior for the same transmitted flight number;
- destination and airport-convergence evidence when available;
- multiple future-path hypotheses around terminal arrivals.

Terminal-arrival aircraft can briefly point toward an observer before making a normal arrival turn. Plane Alerts therefore does not treat a single straight-line projection as sufficient evidence when stronger contradictory evidence exists.

Fresh physical observations remain authoritative. Route history is supporting evidence and cannot hide a genuinely observed close pass.

## Flight-number route history

Historical routing is keyed by the transmitted flight number/callsign, not by aircraft registration.

For example, recurring behavior for `THY1017` is compared with previous `THY1017` routes even when a different airframe operates the flight.

Route storage and persistence are bounded so historical learning cannot consume the live alert loop.

## Prediction Lab

Prediction Lab records forecasts and later compares them with observed outcomes. It is used to investigate:

- ETA accuracy and stability;
- projected versus observed CPA;
- missed close passes;
- false or late cancellations;
- qualify/cancel oscillation;
- route-history mistakes;
- terminal-arrival turns;
- Next60 timing quality;
- regressions introduced by new releases.

Missing ADS-B coverage is explicitly marked unresolved and excluded from accuracy scoring.

### Next 60 Minutes

`/next60` and `/forecast` show expected aircraft in 0–15, 15–30, and 30–60 minute windows.

Short-range live geometry remains the authority. Longer-range historical timing is shadow-only until enough repeatable outcomes exist. Single-day or highly variable history is rejected from scoring.

### Europe Sentinel Network

Shadow-only European sentinel locations collect independent route outcomes and adversarial turn-away examples. Sentinel results never create user alerts.

## AGY investigation worker

A separate **Plane-Alerts-AGY** Railway service performs post-outcome investigation.

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

AGY findings are hypotheses. They must be checked against production telemetry and the current source before a code change is accepted.

The AGY service uses a watchdog/supervisor, persistent Railway volume, account-based authentication, and paid-credit overages disabled. A denied headless command is treated as a recoverable tooling error, not successful goal completion.

## Photography intelligence

Plane Alerts also contains deterministic spotting guidance that can estimate or recommend:

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

## Deployment

Production runs on Railway with MongoDB persistence.

The main service runs Telegram, ADS-B polling, deterministic prediction, route history, photography intelligence, Next60, and the low-rate sentinel sampler.

The AGY service runs the redacted Prediction Lab bridge, goal supervisor/watchdog, persistent authenticated investigation session, and shadow audit helpers.

After main-branch CI succeeds, GitHub Actions checks out that exact tested SHA and uploads it directly to both Railway services. AGY keeps its existing persistent volume and authentication state.

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
scripts/             deployment and audit helpers
tests/               unit, regression and replay tests
docs/error_museum/   preserved production failure cases
.github/workflows/   CI and Railway deployment workflows
```

## Design principle

> **Deterministic code decides what is physically happening. AI may investigate or explain the evidence, but it does not control the live alert decision.**

ADS-B data can be stale, delayed, duplicated, incomplete, or temporarily unavailable. Plane Alerts records uncertainty explicitly instead of inventing certainty.
