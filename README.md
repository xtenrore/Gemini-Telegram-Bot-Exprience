# Plane Alerts

Plane Alerts is a Telegram-based aircraft-spotting alert system. It combines live ADS-B data with deterministic trajectory, closest-point-of-approach (CPA), ETA, route-history and terminal-arrival logic so alerts are based on whether an aircraft is genuinely expected to pass the observer rather than proximity alone.

AI is not part of the live qualification path. It does not decide trajectory, CPA, ETA, pass/no-pass, filter matches, cancellation or notification timing.

Current release candidate: **Plane Alerts v4.3**

Telegram: **[@planebotnotifierbot](https://t.me/planebotnotifierbot)**

## v4.3 — Profiles and aircraft filtering

v4.3 adds a persistent Telegram-native profile and filtering system without moving configuration into a Mini App or website.

### Profiles

`/profiles` opens the profile manager. Each profile stores an independent alert configuration, including location, radius, aircraft selection and advanced filtering rules.

Profiles can be created, activated, edited, renamed, duplicated and deleted. The active profile is persisted per user. Existing users are migrated safely into an initial `Default` profile using their current settings.

The active profile is materialized into the existing runtime configuration collections so the monitoring worker does not need an additional profile lookup during every five-second cycle.

### Aircraft selection

The selector is data-driven and uses canonical ICAO type designators where available. It supports category browsing, pagination and search by type code, manufacturer, model and common aliases.

Current groups include:

- Widebodies
- Narrowbodies
- Regional jets
- Turboprops
- Cargo
- Business jets
- Military
- General aviation
- Helicopters
- Classic / rare
- Other

`All Aircraft` is a logical mode rather than a frozen list. Unknown or newly introduced aircraft types can still qualify when this mode is enabled.

Cargo is treated as a role when operator metadata provides a better signal than airframe type alone, so passenger and freighter use of the same basic aircraft family do not have to be treated identically.

### Advanced rules

Rules follow this deterministic inheritance order:

```text
Profile defaults
  -> Category override
  -> Aircraft-specific override
```

The most specific configured value wins. Missing override fields inherit their parent setting instead of copying entire configuration objects.

Supported rule fields include:

- enabled / disabled
- minimum altitude
- maximum altitude
- airline/operator allow-list
- airline/operator block-list
- optional radius override

Category and aircraft overrides can be reset to their parent settings.

Airline input is normalized locally to canonical operator identities where possible. Common names, IATA codes, ICAO codes and aliases such as `Turkish`, `TK`, `THY` and `Turkish Airlines` resolve deterministically. Unknown three-letter ICAO operator codes can still be stored without adding an AI dependency.

### Runtime integration

The profile filter runs before the existing prediction engine:

```text
ADS-B aircraft
  -> aircraft/category selection
  -> operator rule
  -> altitude rule
  -> profile/category/aircraft override resolution
  -> existing trajectory / CPA / ETA / route qualification
  -> alert lifecycle
  -> Telegram notification
```

Selecting an aircraft only expresses user interest. It never bypasses trajectory, CPA, route-history, terminal-arrival or confidence checks.

Compiled filters are cached by configuration fingerprint. Per-aircraft evaluation is local and deterministic and introduces no database, network or AI request into the alert loop.

## Alert qualification

Plane Alerts does not alert simply because an aircraft is nearby, points toward the observer, or is assigned to a particular destination.

The live engine can consider:

- current position
- heading and groundspeed
- altitude and vertical rate
- position age and update gaps
- recent distance trend
- turn rate and curvature
- projected closest point of approach
- time to CPA
- route history for the transmitted flight number
- destination and airport-convergence evidence when available
- multiple future-path hypotheses around terminal arrivals

Terminal-arrival aircraft can briefly point toward an observer before making a normal arrival turn. Plane Alerts therefore does not treat a single straight-line projection as sufficient evidence when stronger contradictory evidence exists.

Fresh physical observations remain authoritative. Historical route information is supporting evidence and cannot hide a genuinely observed close pass.

## Five-second monitoring cadence

The monitoring path is designed around a nominal five-second cycle for priority users.

Recent cadence protections include:

- batched active-user location and preference reads
- batched approach-state reads per user/cycle
- bounded ADS-B provider refresh time
- continuity snapshots that preserve data age instead of pretending stale data is fresh
- provider-learning work moved off the alert-critical path
- bounded scheduler tolerance so normal sub-second jitter does not create an accidental ten-second gap

The v4.3 profile filter is compiled from the already loaded active preference document, so it does not add a new profile query inside the monitoring cycle.

## Flight-number route history

Historical routing is keyed by the transmitted flight number/callsign rather than aircraft registration. A recurring flight such as `THY1017` is compared with previous `THY1017` routes even when a different airframe operates it.

Route storage and persistence are bounded so historical learning cannot consume the live alert path.

## Prediction Lab

Prediction Lab records forecasts and later compares them with observed outcomes. It is used to investigate:

- ETA accuracy and stability
- projected versus observed CPA
- missed close passes
- false or late cancellations
- qualify/cancel oscillation
- route-history mistakes
- terminal-arrival turns
- Next60 timing quality
- regressions introduced by new releases

Missing ADS-B coverage is marked unresolved and excluded from accuracy scoring.

`/next60` and `/forecast` show expected aircraft in 0–15, 15–30 and 30–60 minute windows. Short-range live geometry remains authoritative; longer-range historical expectations remain shadow-only until enough repeatable outcomes exist.

## Photography intelligence

Plane Alerts also includes deterministic spotting guidance for camera settings, framing, sun position, atmospheric conditions, upper-air conditions, contrail probability and shooting-window timing. This layer remains usable when AI services are unavailable.

## Telegram commands

| Command | Purpose |
| --- | --- |
| `/start` | Initial setup |
| `/profiles` | Create, switch and manage alert profiles |
| `/status` | Show monitoring status |
| `/next60` | Aircraft expected in the next 60 minutes |
| `/forecast` | Alias for `/next60` |
| `/location` | Update the active spotting location |
| `/preferences` | Configure aircraft selection and advanced filters |
| `/camera` | Configure camera body |
| `/lens` | Configure lens |
| `/photo` | Current shooting guidance |
| `/conditions` | Weather, sun and atmospheric information |
| `/spotting` | Spotting tools |
| `/help` | Command help |

The private `/agy` console is owner-only and is not part of normal user configuration.

## Persistence

Production uses MongoDB. Profiles are isolated by `user_id` and an immutable profile identifier, with the active profile identifier stored on the user document.

The migration is idempotent: existing `locations` and `preferences` data is copied into a default profile without deleting the original configuration. Activating a profile materializes its location and preferences into the existing runtime collections for backward compatibility.

## Aircraft catalogue maintenance

The aircraft registry is separate from Telegram menu code. Aircraft entries contain canonical type code, manufacturer, model, family, categories and aliases, so adding or refreshing catalogue data does not require rewriting callback logic.

The project uses local registry data at runtime. A catalogue refresh should be generated outside the live alert loop from a reliable aviation reference source and committed as application data; live aircraft checks must not depend on an external type-designator API.

## Testing

The pull-request CI path performs:

```text
python -m compileall -q app vercel_runtime worker.py
pytest -q
python scripts/benchmark_v42.py
pip check
```

v4.3 adds regression coverage for profile migration, persistence, activation, deletion fallback, duplication, multi-user isolation, logical All Aircraft behavior, category/type selection, rule inheritance, altitude filtering, operator aliases, allow/block lists, unknown aircraft/operator behavior, search and hot-loop filter performance.

Changes are not merged when CI fails.

## Deployment

Production runs on Railway with MongoDB persistence. The main service runs Telegram, shared ADS-B polling, deterministic prediction, route history, photography intelligence and Next60. A separate AGY service performs post-outcome investigation.

Deployment is separate from feature implementation. This v4.3 branch is not deployed simply because the code or pull request exists.

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
  aircraft/          ADS-B providers, aircraft registry and deterministic filters
  bot/               Telegram commands, profile flows and messages
  intelligence/      trajectory, route history and terminal-arrival logic
  photography/       deterministic camera guidance
  worker/            shared production polling and cadence guards
scripts/             deployment, verification and audit helpers
tests/               unit, regression and replay tests
docs/error_museum/   preserved production failure cases
.github/workflows/   CI and Railway workflows
```

## Design principle

**Deterministic code decides what is physically happening. AI may investigate or explain evidence, but it does not control the live alert decision.**

ADS-B data can be stale, delayed, duplicated, incomplete or temporarily unavailable. Plane Alerts records uncertainty explicitly instead of inventing certainty.
