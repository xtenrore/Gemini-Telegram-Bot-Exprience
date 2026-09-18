# Plane Alerts

**Aircraft spotting alerts that try to predict the pass, not just detect the plane.**

Plane Alerts watches live ADS-B traffic around your spotting location and asks a stricter question than a normal proximity bot:

> **Is this aircraft actually likely to pass close enough to be worth getting the camera ready?**

It combines live trajectory geometry, deterministic future-path hypotheses, recent flight-number route history, prediction confidence, photography conditions, and a continuous Prediction Lab that compares forecasts with what actually happened.

The current production generation is **Plane Alerts v4.2**.

### Try it

Message **[@planebotnotifierbot](https://t.me/planebotnotifierbot)** on Telegram and run `/start`.

---

## What v4.2 changes

### Terminal-arrival qualification

v4.2 addresses a difficult false-alert class seen around Istanbul Airport and other terminal areas: an arriving aircraft can temporarily point directly at an observer even though a normal arrival turn will take it away before it reaches the observer.

A straight-line CPA is therefore no longer enough by itself to promote a terminal-arrival candidate into an early notification when strong contradictory evidence exists.

The live predictor now feeds a bounded deterministic qualification ensemble containing, when applicable:

- constant-heading continuation
- observed-turn continuation
- recent-curvature continuation
- shallow left/right turn envelopes
- moderate left/right turn envelopes
- airport-convergence paths for evidence-backed arrivals
- matched historical-route continuation

Each path receives an explicit deterministic weight. The **ensemble pass score** is the fraction of weighted hypotheses whose projected CPA enters the user's radius. It is a statistical score, not an AI-generated probability.

### Evidence-based arrival state

`TERMINAL_ARRIVAL` is not set from destination alone. The classifier requires multiple supporting signals such as route plausibility, distance and trend toward the airport, altitude, descent, groundspeed, heading compatibility, and recent route behavior.

When destination metadata is unavailable, strong partial-route similarity plus descent/altitude evidence may create the weaker `ARRIVAL_LIKELY_HISTORY` state. Missing information increases uncertainty instead of being treated as proof.

### Expected-turn state

Arrival candidates distinguish:

- `EXPECTED_TURN_PENDING`
- `TURN_STARTED`
- `TURN_CONFIRMED`
- `TURN_DID_NOT_OCCUR`

Expected-turn authority is deliberately bounded. If the aircraft does not make the expected turn within the calculated window, airport/history hypotheses stop dominating qualification and live-motion hypotheses regain authority. This prevents route history from hiding a genuine close pass just because yesterday's aircraft turned away.

### Shadow qualification, persistence and hysteresis

A first projected pass can remain internal while evidence stabilizes. Terminal-arrival candidates normally need three fresh qualifying samples; ordinary candidates use two. A data gap longer than the fresh-confirmation window resets pending confirmations.

Qualification and cancellation use different thresholds so small CPA changes do not produce qualify/cancel oscillation. Stale ADS-B data is uncertainty and cannot confirm a predicted turn.

Once an encounter is strongly marked passed, the same encounter cannot be reopened by a stale/source-switched inbound vector. A later alert requires meaningful separation and a genuinely new inbound approach.

The thresholds and model are documented in `docs/V4_2_TERMINAL_ARRIVAL.md`, with production failure evidence preserved under `docs/error_museum/`.

### Performance bounds

The expensive aircraft-global motion paths are shared across users. Only observer-specific CPA reduction is repeated per user.

The v4.2 CI benchmark simulates 250 observers against one nine-path ensemble and enforces bounded path/encounter caches. This keeps the new logic suitable for the existing shared multi-user poller instead of multiplying full simulations by user count.

---

## Prediction Lab

Every useful prediction can become an experiment. Plane Alerts records what it expected, waits for reality, and then measures the difference.

The lab focuses on:

- ETA accuracy and ETA stability
- projected closest point of approach (CPA)
- false or late alert cancellations
- missed close passes
- qualify/cancel oscillation
- route-history mistakes
- 30–60 minute forecast calibration
- regressions introduced by new prediction changes

Missing ADS-B coverage is **not** counted as a successful prediction and is **not** counted as a miss. It is stored as unresolved coverage.

### Next 60 Minutes

Use either:

- `/next60`
- `/forecast`

The response is split into **0–15**, **15–30**, and **30–60 minute** sections.

Short-range live geometry remains the authority. Longer-range entries are history/shadow based and deliberately use wider timing windows instead of pretending to know an exact ETA before the evidence supports one. Experimental 30–60 minute forecasting does not become an alert-critical decision merely because it appears in the forecast UI.

### Europe Sentinel Network

Prediction Lab is not limited to the saved locations of real users. A shadow-only sentinel network rotates through European traffic regions to collect independent prediction outcomes without turning sentinel traffic into user alerts.

### Adversarial virtual observers

When an observed route makes a meaningful turn, Prediction Lab can place a virtual observer farther along the aircraft's pre-turn course. That creates the exact kind of adversarial case v4.2 is designed to handle:

```text
aircraft temporarily points toward observer
                    ↓
       straight-line CPA looks close
                    ↓
      expected arrival turn occurs
                    ↓
actual aircraft remains outside alert radius
```

These cases remain shadow-only and are evaluated as regression evidence.

---

## Alert engine

Plane Alerts does not qualify a pass from current distance, destination, heading, or a single straight-line projection alone.

For relevant aircraft it maintains bounded recent motion history and evaluates information such as:

- current distance and distance trend
- heading and groundspeed
- turn rate and recent curvature
- acceleration/deceleration
- vertical rate and altitude trend
- horizontal and slant CPA
- time to CPA
- ADS-B age/staleness and update gaps
- trajectory confidence
- plausible future turn envelopes
- airport convergence when an arrival is supported by multiple signals
- recent route behavior for the same flight number
- partial live-track similarity to bounded historical routes

Active alerts are recalculated continuously. A prediction that stops qualifying can be updated or cancelled instead of continuing a countdown to a pass that will never happen.

## Flight-number route history

Historical behavior is keyed by the **flight number / transmitted callsign**, not by aircraft registration.

For example, the useful recurring identity is a flight such as `TK1017`, not whichever tail number happens to operate it today.

Recent routes can help identify recurring corridors and turns, but history is supporting evidence only. If recent routes disagree, historical confidence drops. If today's live track diverges from history, live motion regains authority instead of divergence becoming a permanent veto.

Route samples, caches, motion history, ensemble size, and encounter state are bounded.

## Shared ADS-B polling

Nearby users reuse shared regional aircraft snapshots rather than each user independently querying providers.

The production poller:

- groups compatible users into shared regions
- rotates free/public ADS-B sources
- keeps OpenSky as a fallback rather than querying it every cycle
- briefly reuses a previous non-empty snapshot during transient feed gaps
- keeps active regions on a faster cadence
- slows quiet discovery regions
- keeps trajectory memory bounded

A cached or old position cannot create a brand-new alert after the live-position freshness limit. Missing coverage is never interpreted as evidence that an expected turn happened.

---

## Photography intelligence

Plane Alerts also contains a deterministic aircraft-photography layer. Depending on available information and the saved camera/lens profile it can estimate or recommend:

- shutter speed
- aperture
- Auto ISO limits
- focal length
- frame fill and clipping risk
- angular motion
- sun position and lighting direction
- haze / atmospheric clarity
- upper-air conditions
- contrail formation and persistence
- useful shooting-window timing

The same design rule applies here: physical calculations should continue working even if AI is unavailable.

---

## Telegram commands

| Command | Purpose |
| --- | --- |
| `/start` | Initial Plane Alerts setup |
| `/status` | Show monitoring status |
| `/next60` | Show planes expected in the next 60 minutes |
| `/forecast` | Alias for `/next60` |
| `/location` | Set the spotting location |
| `/preferences` | Configure aircraft and alert preferences |
| `/camera` | Set the camera body |
| `/lens` | Set the lens |
| `/photo` | Show current shooting guidance |
| `/conditions` | Show weather, sun and atmospheric information |
| `/spotting` | Open spotting tools |
| `/help` | Show command help |

The private `/agy` console is owner-only and is not a general-purpose host shell.

---

## Prediction improvement loop

Plane Alerts uses a separate **Plane-Alerts-AGY** Railway worker for prediction investigation.

```text
Live ADS-B
   ↓
Deterministic production predictor
   ↓
Recorded expectation
   ↓
Actual observed outcome
   ↓
Prediction Lab
   ↓
AGY investigation
   ↓
CHATGPT_HANDOFF_JSON
   ↓
Independent verification
   ↓
Regression / replay tests
   ↓
Safer candidate change
```

AGY findings are **hypotheses, not automatic production fixes**. A suggested change is expected to be independently checked against telemetry and source code, reproduced where practical, and protected by tests before deployment.

### No AI in the alert-critical path

Runtime aircraft prediction does not rely on Gemini, ChatGPT, Claude, or any other AI model to decide trajectory, CPA, ETA, pass/no-pass, turns, route prediction, alert qualification, cancellation, or notification timing.

AI tooling may investigate completed outcomes or explain deterministic evidence after the fact, but it does not delay early alerts, CAMERA READY, or PHOTO NOW.

---

## Production architecture

Production runs on Railway with MongoDB persistence.

The main service contains:

- FastAPI health/admin API
- Telegram bot
- shared ADS-B monitor
- deterministic trajectory/CPA engine
- v4.2 terminal-arrival ensemble qualifier
- route-history system
- photography intelligence
- Next 60 Minutes user command
- low-rate Europe Sentinel sampler

The separate AGY worker contains Prediction Lab investigation and durable handoff tooling. Exact saved user coordinates and database credentials are kept out of its model context.

---

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

The practical minimum is a Telegram bot token and a MongoDB connection. Free/public ADS-B provider endpoints already have defaults in the project configuration.

## Project layout

```text
app/
  aircraft/          ADS-B providers and aircraft normalization
  bot/               Telegram commands and messages
  intelligence/      trajectory, route-history, arrival ensemble and environment logic
  photography/       camera and spotting guidance
  worker/            shared production polling
  sentinel_network.py
  sentinel_shadow.py
  next_hour_shadow.py
  agy_prediction_bridge.py

docs/error_museum/   production prediction regression evidence
scripts/              Railway / AGY helpers and v4.2 benchmark
tests/                regression, trajectory, alert and Prediction Lab tests
.github/workflows/    CI and deployment workflows
```

---

## Design principle

> **Deterministic code decides what is physically happening. AI only explains, investigates, or proposes improvements.**

## Data limitations

ADS-B is observational data. Feeds can be delayed, incomplete, duplicated, stale, or temporarily wrong, and receiver coverage differs by region.

Plane Alerts therefore records uncertainty rather than silently turning missing data into success. Confidence checks, bounded motion history, multiple providers, airport/route context, deterministic future-path ensembles, sentinel observations, replay cases, and measured outcomes are intended to make the system improve without pretending that the underlying data is perfect.
