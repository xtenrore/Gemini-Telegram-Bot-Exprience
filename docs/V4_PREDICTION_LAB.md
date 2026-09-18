# Plane Alerts v4.0 Prediction Lab

This document describes the shadow-learning systems introduced with Plane Alerts v4.0.

## User Next 60 Minutes

`/next60` and `/forecast` combine two evidence sources:

- live production approach state when deterministic CPA/ETA already exists;
- flight-number timing history for longer-range shadow expectations.

Live geometry overrides a historical entry for the same flight. 30–60 minute history remains shadow-only and is shown as a timing window rather than false exact precision.

## Europe Sentinel Network

The production service rotates a single free public ADS-B request every 30 seconds across eight fixed European observation regions. Sentinel acquisition is independent of user alerts and does not create notifications.

Current regions: Istanbul, London, Frankfurt, Paris, Amsterdam, Madrid, Rome and Vienna.

The sampler stores bounded, callsign-keyed route traces in `prediction_sentinel_routes` for up to eight days. One region is queried at a time; the public provider is rotated as well.

## Adversarial observers

Prediction Lab scans observed sentinel routes for meaningful turns. When a route turns, a virtual observer may be placed farther along the pre-turn bearing. The resulting replay case represents a location where a naive straight-line predictor could think the aircraft will pass, while the actual observed path turns away and remains outside the alert radius.

These cases are written to `prediction_lab_audit` as `sentinel_adversarial_turn_away` records.

## Europe Next-Hour audit

Recent callsign timing at each sentinel region is used to create shadow expectations for the next hour. Later observations resolve those expectations with actual timing and closest distance.

If no current route observation exists after the evaluation window, the outcome is `unresolved_coverage` and is excluded from accuracy scoring.

## Safety boundaries

- Sentinel output cannot create or cancel production user alerts.
- No AI decides trajectory, CPA, ETA, pass/no-pass or alert timing.
- Sentinel collection uses only free/public ADS-B providers.
- AGY receives redacted Prediction Lab context; exact user locations and database credentials are removed.
- AGY findings require independent verification before production changes are accepted.
