"""Project-wide redacted AGY context for Plane Alerts v4.4.

The existing Prediction Lab bridge remains the privacy boundary and durable
handoff transport. This adapter enriches its snapshot with bounded evidence
from the rest of Plane Alerts so AGY can investigate system-wide behavior
without receiving credentials, exact observer coordinates, or live authority.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app import agy_prediction_bridge as base


def _decision_groups(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    failures: list[dict[str, Any]] = []
    successful: list[dict[str, Any]] = []
    ambiguous: list[dict[str, Any]] = []
    for row in rows:
        subsystem = str(row.get("subsystem") or "")
        event = str(row.get("event") or "")
        decision = str(row.get("decision") or "")
        reason = str(row.get("decision_reason_code") or "")
        evidence = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
        if subsystem == "user_feedback" and decision == "non_helpful":
            failures.append(row)
        elif decision in {"delivery_failed"} or "error" in reason or "failed" in reason:
            failures.append(row)
        elif reason in {"terminal_arrival_transient_alignment", "active_turn_transient_alignment"}:
            ambiguous.append(row)
        elif subsystem == "prediction":
            score = evidence.get("ensemble_pass_score")
            try:
                numeric = float(score)
            except (TypeError, ValueError):
                numeric = None
            if numeric is not None and 0.30 < numeric < 0.85:
                ambiguous.append(row)
        if subsystem == "telegram_alert" and decision == "delivered" and event in {"delivery", "passed"}:
            successful.append(row)
        elif subsystem == "contrail" and reason == "google_forecast_primary":
            successful.append(row)
    return {
        "failure_or_negative_samples": failures[:120],
        "ambiguous_shadow_candidates": ambiguous[:120],
        "successful_samples": successful[:120],
    }


def build_supervisor_context_snapshot() -> dict[str, Any]:
    payload = base.build_context_snapshot()
    now = datetime.now(timezone.utc)

    decisions = base._recent("decision_records", "timestamp", 900)
    feedback = base._recent("feedback", "updated_at", 350)
    system_status = base._recent("system_status", "updated_at", 80)
    agy_findings = base._recent("agy_findings", "created_at_epoch", 160)
    shadow_reviews = base._recent("agy_shadow_reviews", "reviewed_at", 250)
    admin_audit = base._recent("admin_audit", "created_at", 120)

    groups = _decision_groups(decisions)
    subsystem_counts: dict[str, int] = {}
    reason_counts: dict[str, int] = {}
    for row in decisions:
        subsystem = str(row.get("subsystem") or "unknown")
        reason = str(row.get("decision_reason_code") or "unknown")
        subsystem_counts[subsystem] = subsystem_counts.get(subsystem, 0) + 1
        reason_counts[reason] = reason_counts.get(reason, 0) + 1

    feedback_counts: dict[str, int] = {}
    for row in feedback:
        label = str(row.get("feedback_label") or row.get("feedback") or "unknown")
        feedback_counts[label] = feedback_counts.get(label, 0) + 1

    payload["schema"] = "plane-alerts-supervisor-v4.4"
    payload["generated_at"] = now.isoformat()
    payload["purpose"] = (
        "Project-wide evidence for Plane Alerts reliability investigation: prediction, ETA/CPA, terminal arrivals, "
        "Next 60 Minutes, photography recommendations, contrail forecasts, Telegram UX/callback health, profile flows, "
        "runtime cadence/resource health, and user feedback. AGY is analysis/shadow-review only and never controls alerts."
    )
    payload["limitations"] = list(payload.get("limitations") or []) + [
        "Decision Recorder and supervisor samples are bounded/TTL-retained; absence of a record is not proof that an event never occurred.",
        "AGY opinions are hypotheses. They cannot send, suppress, cancel, or delay a production aircraft alert.",
        "Missing ADS-B coverage must remain unknown and must never be counted as prediction success or failure.",
        "Exact observer/user coordinates, authentication material, tokens, and secrets are intentionally removed.",
    ]
    summary = dict(payload.get("summary") or {})
    summary.update({
        "decision_records": len(decisions),
        "decision_subsystem_counts": subsystem_counts,
        "decision_reason_counts": reason_counts,
        "feedback_records": len(feedback),
        "feedback_counts": feedback_counts,
        "recent_agy_findings": len(agy_findings),
        "agy_shadow_reviews": len(shadow_reviews),
        "runtime_status_records": len(system_status),
    })
    payload["summary"] = summary
    payload["decision_records"] = decisions
    payload["user_feedback"] = feedback
    payload["runtime_health"] = system_status
    payload["recent_agy_findings"] = agy_findings
    payload["agy_shadow_reviews"] = shadow_reviews
    payload["admin_audit"] = admin_audit
    payload.update(groups)

    base._atomic_json(base.CONTEXT_FILE, payload)
    return payload
