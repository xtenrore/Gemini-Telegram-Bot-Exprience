# Plane Alerts v4.2 — Terminal Arrival Qualification

v4.2 adds a deterministic qualification layer around the existing CPA predictor rather than replacing it.

## Why

Production telemetry on 2026-09-18 showed arrival aircraft temporarily producing close projected CPAs before later turning toward Istanbul Airport. The existing system had three structural weaknesses:

1. a live projected CPA could regain authority after only a short route-resolution grace;
2. airport-arrival behavior was represented mostly as veto rules instead of competing future-path hypotheses;
3. historical divergence/inconsistency could itself become a veto, which is backwards — divergence should reduce historical authority and return control to live motion.

## v4.2 decision flow

A live CPA candidate now remains internal while a bounded deterministic ensemble is evaluated. The ensemble contains constant-heading, observed-turn, recent-curvature, shallow and moderate turn-envelope, airport-convergence, and matched historical-continuation hypotheses.

Each path gets a deterministic weight. `pass_score` is the sum of weights for hypotheses whose CPA enters the user's radius divided by the total hypothesis weight. It is not an AI probability.

The default promotion thresholds are:

- normal candidate qualification: `0.68`
- terminal-arrival qualification: `0.76`
- active-alert cancellation threshold: `0.42`
- normal fresh confirmations: `2`
- terminal-arrival fresh confirmations: `3`

These are centralized in `app/intelligence/route_guard_v42.py`. Qualification is intentionally harder than cancellation to provide hysteresis.

## Terminal-arrival evidence

`TERMINAL_ARRIVAL` requires multiple signals. Destination alone is insufficient. Evidence includes route plausibility, distance to destination, destination-distance trend, descent/altitude, groundspeed, and heading compatibility. When destination metadata is absent, strong partial-route similarity plus descent/low-altitude behavior can produce the weaker `ARRIVAL_LIKELY_HISTORY` state.

## Expected turn state

The guard distinguishes:

- `EXPECTED_TURN_PENDING`
- `TURN_STARTED`
- `TURN_CONFIRMED`
- `TURN_DID_NOT_OCCUR`

Airport-turn evidence is bounded in time. If the expected turn does not occur within the calculated window, history/airport expectations cannot suppress indefinitely and live ensemble evidence regains authority.

## Encounter invariant

Once a pass is strongly observed, the same encounter cannot be reopened by a stale or source-switched inbound vector. A new encounter requires both meaningful separation and a later genuine inbound trend.

## Data quality

Stale/missing ADS-B data reduces qualification evidence. Missing coverage is never treated as confirmation that a predicted turn happened.

## Performance bounds

Motion-path hypotheses are observer-independent and cached per aircraft state so nearby users reuse the same simulated futures. The cache is capped at 128 aircraft states with a 30-second TTL. Per-observer encounter state is capped at 2,048 entries with a 30-minute TTL. The ensemble itself is capped at nine simulated motion paths plus at most one historical continuation hypothesis.

## Replay evidence

`docs/error_museum/v42-ist-arrival-false-alert-2026-09-18.json` records the production decision evidence for the THY2GN and THY7ER incidents. Raw ADS-B sample sequences were not retained, so the artifact is explicitly a decision-level reconstruction rather than an exact geometric track replay.
