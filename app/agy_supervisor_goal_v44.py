"""Plane Alerts v4.4 project-wide AGY supervisor goal.

This only changes what the isolated AGY service investigates. It grants no
production write/deploy authority and does not participate in live alerts.
"""
from __future__ import annotations

import os

V44_SUPERVISOR_GOAL = """Continuously audit Plane Alerts as a reliability supervisor using the redacted production context in /agy-state/prediction-lab/context/latest.json and the repository in /app.

Investigate meaningful evidence across: trajectory/CPA/ETA and terminal-arrival decisions; false, missed, late, or oscillating alerts; route history and Next 60 Minutes shadow outcomes; five-second cadence and runtime health; provider/ADS-B freshness and coverage ambiguity; Telegram callbacks, stuck/dead-end profile flows and repeated actions; profile synchronization/inheritance; photography settings, framing, shooting windows and Non-helpful feedback; contrail forecasts including Google-vs-deterministic disagreement; Mini App/API errors; memory/CPU/Mongo/provider latency; exceptions and repeated warnings.

Treat user feedback as first-class evidence and link findings to Decision Recorder IDs when available. Also sample successful cases so calibration is not failure-only. Never count missing ADS-B coverage as a success or miss.

For genuinely ambiguous deterministic decisions you may form a SHADOW opinion only: allow, suppress, wait, or cancel. The live deterministic decision remains authoritative. Never modify alert decisions, trajectory, CPA, ETA, pass/no-pass, cancellation, notification timing, or production state based on AI opinion. Do not deploy.

When a strong reproducible issue exists, immediately run /app/scripts/agy_record_finding.py with subsystem, severity, summary, concrete evidence, timestamps/telemetry references, reproduction, suspected cause, confidence, Decision Recorder IDs, and suggested investigation/fix. Findings are hypotheses for independent ChatGPT verification. Avoid duplicate findings already represented in the context or handoff history. When appropriate, identify an Error Museum regression case.

Use available baseline quota productively on real Plane Alerts evidence, but never deliberately waste quota. Never use paid AI/API credits. Prefer deterministic/statistical fixes and replayable tests. Normal Plane Alerts must remain functional when AGY is unavailable."""


def install_agy_supervisor_goal_v44(supervisor) -> None:
    # An explicitly configured deployment goal remains authoritative. Otherwise
    # upgrade old persisted Prediction-Lab-only goals to the project-wide v4.4
    # supervisor brief once per service image.
    if os.getenv("AGY_GOAL", "").strip():
        return
    current = str(getattr(supervisor, "goal", "") or "")
    if "project-wide AGY supervisor" in current and "Decision Recorder" in current:
        return
    supervisor.goal = V44_SUPERVISOR_GOAL
    supervisor.next_run_at = 0.0
    try:
        supervisor._save()
    except Exception:
        pass
