# Plane Alerts

**Aircraft spotting alerts that try to predict the pass, not just detect the plane.**

Plane Alerts watches live ADS-B traffic around your spotting location and asks a stricter question than a normal proximity bot:

> **Is this aircraft actually likely to pass close enough to be worth getting the camera ready?**

It combines live trajectory geometry, recent flight-number route history, prediction confidence, photography conditions, and a continuous Prediction Lab that compares forecasts with what actually happened.

The current production generation is **Plane Alerts v4.0**.

### Try it

Message **[@planebotnotifierbot](https://t.me/planebotnotifierbot)** on Telegram and run `/start`.

---

## What v4.0 adds

### Prediction Lab

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

Short-range live geometry remains the authority. Longer-range entries are history/shadow based and deliberately use wider timing windows instead of pretending to know an exact ETA before the evidence supports one.

### Europe Sentinel Network

Prediction Lab is no longer limited to the saved locations of real users.

A shadow-only sentinel network rotates through eight European traffic regions:

- Istanbul / Marmara
- London / South East
- Frankfurt / Rhine-Main
- Paris / Île-de-France
- Amsterdam / Benelux
- Madrid / Central Spain
- Rome / Central Italy
- Vienna / Central Europe

The network makes **one free public ADS-B query every 30 seconds in total**, rotating the region and provider. It does not create user alerts.

This gives Prediction Lab independent traffic from different route structures and ATC environments without multiplying provider traffic by the number of users.

### Adversarial virtual observers

The lab also creates locations specifically designed to expose prediction failures.

When an observed route makes a meaningful turn, Plane Alerts can place a virtual observer farther along the aircraft's **pre-turn course**. That creates a difficult replay case:

```text
aircraft appears to continue toward virtual observer
                    ↓
              route turns away
                    ↓
actual aircraft never enters the alert radius
```

These cases are useful for catching the classic false alert where a straight-line predictor thinks a plane is coming toward the observer even though it is about to turn for an airport or route transition.

They remain shadow-only and are fed into the same Prediction Lab audit data as real outcomes.

---

## Alert engine

Plane Alerts does not qualify a pass from current distance, destination, or heading alone.

For relevant aircraft it maintains bounded recent motion history and evaluates information such as:

- current distance
- distance trend
- heading and groundspeed
- turn rate
- acceleration/deceleration
- vertical rate
- projected path
- horizontal and slant CPA
- time to CPA
- ADS-B age/staleness
- update consistency
- trajectory confidence
- recent route behaviour for the same flight number

Possible states include **Approaching**, **Passing nearby**, **Moving away**, **Will not approach**, **Prediction uncertain**, **Turning away**, **Trajectory changed**, and **Passed**.

Active alerts are recalculated continuously. A prediction that stops qualifying can be updated or cancelled instead of continuing a countdown to a pass that will never happen.

## Flight-number route history

Historical behaviour is keyed by the **flight number / transmitted callsign**, not by aircraft registration.

For example, the useful identity for a recurring route is the flight such as `TK1017`, not whichever tail number happens to operate it today.

Recent routes can help identify recurring turns and decision points, but historical data is supporting evidence rather than permission to ignore live geometry.

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

The Europe Sentinel Network is separate and deliberately low-rate so Prediction Lab coverage does not turn into an API request storm.

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
Production predictor
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

New findings are persisted immediately so a ChatGPT verification task does not need to start at exactly the same minute as the AGY investigation.

### No paid AI credits

Runtime aircraft prediction does not rely on paid AI APIs.

The Antigravity worker is configured around the authenticated Google account with paid-credit overages disabled. It does not intentionally fall back to Claude, GPT, or paid Gemini API-key usage when account quota is exhausted.

If the account quota is exhausted, the AGY supervisor waits for the quota refresh plus a safety delay rather than purchasing more inference.

---

## Production architecture

Production runs on Railway with MongoDB persistence.

The main service contains:

- FastAPI health/admin API
- Telegram bot
- shared ADS-B monitor
- deterministic trajectory/CPA engine
- route-history system
- photography intelligence
- Next 60 Minutes user command
- low-rate Europe Sentinel sampler

The separate AGY worker contains:

- Prediction Lab redacted context bridge
- next-hour expectation/outcome evaluation
- Europe Sentinel outcome evaluation
- adversarial virtual-observer generation
- Antigravity investigation loop
- durable ChatGPT handoff

Exact saved user coordinates and database credentials are kept out of the AGY model context.

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
  intelligence/      trajectory, route-history and environment logic
  photography/       camera and spotting guidance
  worker/            shared production polling
  sentinel_network.py
  sentinel_shadow.py
  next_hour_shadow.py
  agy_prediction_bridge.py

scripts/              Railway / AGY helpers
tests/                regression, trajectory, alert and Prediction Lab tests
docs/                 technical notes
.github/workflows/    CI and deployment workflows
```

---

## Design principle

> **Deterministic code decides what is physically happening. AI only explains, investigates, or proposes improvements.**

Runtime AI must never be the component that decides trajectory, CPA, ETA, pass/no-pass, or alert timing.

## Data limitations

ADS-B is observational data. Feeds can be delayed, incomplete, duplicated, stale, or temporarily wrong, and receiver coverage differs by region.

Plane Alerts therefore records uncertainty rather than silently turning missing data into success. Confidence checks, bounded motion history, multiple providers, route context, sentinel observations, replay cases, and measured outcomes are all intended to make the system improve without pretending that the underlying data is perfect.
