#!/usr/bin/env python3
"""Persist a structured Plane Alerts v4.4 AGY finding.

Every AGY finding is a hypothesis for independent verification. The script
writes a durable volume inbox, emits CHATGPT_HANDOFF_JSON to Railway logs, and
copies to MongoDB when available. A bounded fingerprint ledger prevents AGY
from repeatedly handing off the same underlying finding.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

STATE_DIR = Path(os.getenv("AGY_STATE_DIR", "/agy-state"))
INBOX = STATE_DIR / "prediction-lab" / "findings.jsonl"
SEQ_FILE = STATE_DIR / "prediction-lab" / "finding-seq.txt"
FINGERPRINTS = STATE_DIR / "prediction-lab" / "finding-fingerprints.json"
FINGERPRINT_TTL_DAYS = 14
FINGERPRINT_MAX = 4096


def _next_seq() -> int:
    SEQ_FILE.parent.mkdir(parents=True, exist_ok=True)
    with SEQ_FILE.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.seek(0)
        raw = handle.read().strip()
        seq = int(raw or "0") + 1
        handle.seek(0)
        handle.truncate()
        handle.write(str(seq))
        handle.flush()
        os.fsync(handle.fileno())
        return seq


def _append(payload: dict[str, Any]) -> None:
    INBOX.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
    with INBOX.open("a", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())


def _load_json_arg(raw: str) -> Any:
    if not raw.strip():
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON argument: {exc}") from exc


def _csv(raw: str) -> list[str]:
    return [item.strip() for item in str(raw or "").split(",") if item.strip()]


def _fingerprint(subsystem: str, summary: str, reproduction: str, decision_ids: list[str]) -> str:
    canonical = json.dumps(
        {
            "subsystem": subsystem.strip().lower(),
            "summary": " ".join(summary.lower().split()),
            "reproduction": " ".join(reproduction.lower().split()),
            "decision_ids": sorted(decision_ids),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def _claim_fingerprint(value: str) -> bool:
    """Atomically claim fingerprint; return False when a recent duplicate exists."""
    FINGERPRINTS.parent.mkdir(parents=True, exist_ok=True)
    now = time.time()
    cutoff = now - FINGERPRINT_TTL_DAYS * 86400
    with FINGERPRINTS.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.seek(0)
        try:
            raw = json.loads(handle.read() or "{}")
        except Exception:
            raw = {}
        ledger = {str(k): float(v) for k, v in dict(raw).items() if float(v) >= cutoff}
        if value in ledger:
            return False
        ledger[value] = now
        if len(ledger) > FINGERPRINT_MAX:
            ledger = dict(sorted(ledger.items(), key=lambda item: item[1], reverse=True)[:FINGERPRINT_MAX])
        handle.seek(0)
        handle.truncate()
        json.dump(ledger, handle, separators=(",", ":"), sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
        return True


def _mongo_copy(payload: dict[str, Any]) -> None:
    uri = os.getenv("MONGO_URI", "").strip()
    if not uri:
        return
    try:
        from pymongo import MongoClient

        client = MongoClient(uri, serverSelectionTimeoutMS=4000, connectTimeoutMS=4000)
        db_name = os.getenv("DATABASE_NAME", "aircraft_bot")
        client[db_name]["agy_findings"].update_one(
            {"finding_fingerprint": payload["finding_fingerprint"]},
            {"$setOnInsert": payload},
            upsert=True,
        )
        client.close()
    except Exception as exc:  # Durable volume + logs remain authoritative fallback.
        print(f"AGY_FINDING_MONGO_WARN {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--subsystem", default="prediction")
    parser.add_argument("--severity", required=True, choices=["info", "low", "medium", "high", "critical"])
    parser.add_argument("--summary", required=True)
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--affected", default="")
    parser.add_argument("--timestamps", default="")
    parser.add_argument("--production-refs", default="")
    parser.add_argument("--reproduction", default="")
    parser.add_argument("--suspected-cause", default="")
    parser.add_argument("--confidence", default="medium", choices=["low", "medium", "high"])
    parser.add_argument("--before-metrics", default="")
    parser.add_argument("--after-metrics", default="")
    parser.add_argument("--decision-record-ids", default="")
    parser.add_argument("--suggested-investigation", default="")
    parser.add_argument("--suggested-fix", default="")
    parser.add_argument("--error-museum-case", default="")
    parser.add_argument("--related-finding", default="")
    parser.add_argument("--flight", default="")
    parser.add_argument("--category", default="")  # legacy alias retained
    parser.add_argument("--source", default="antigravity")
    args = parser.parse_args()

    subsystem = str(args.subsystem or args.category or "prediction").strip().lower()
    decision_ids = _csv(args.decision_record_ids)
    fingerprint = _fingerprint(subsystem, args.summary, args.reproduction, decision_ids)
    if not _claim_fingerprint(fingerprint):
        print(
            "AGY_FINDING_DUPLICATE "
            + json.dumps({"finding_fingerprint": fingerprint, "summary": args.summary}, separators=(",", ":")),
            flush=True,
        )
        return 0

    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "schema": "plane-alerts-chatgpt-handoff-v4.4",
        "seq": _next_seq(),
        "finding_id": f"agy-{now.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}",
        "finding_fingerprint": fingerprint,
        "created_at": now.isoformat(),
        "created_at_epoch": time.time(),
        "subsystem": subsystem,
        "severity": args.severity,
        "summary": args.summary.strip(),
        "evidence": args.evidence.strip(),
        "affected": args.affected.strip(),
        "relevant_timestamps": _csv(args.timestamps),
        "production_telemetry_references": _csv(args.production_refs),
        "reproduction": args.reproduction.strip(),
        "suspected_cause": args.suspected_cause.strip(),
        "confidence": args.confidence,
        "before_metrics": _load_json_arg(args.before_metrics),
        "after_metrics": _load_json_arg(args.after_metrics),
        "decision_record_ids": decision_ids,
        "suggested_investigation": args.suggested_investigation.strip(),
        "suggested_fix": args.suggested_fix.strip(),
        "existing_error_museum_case": args.error_museum_case.strip(),
        "related_finding": args.related_finding.strip(),
        "flight": args.flight.strip(),
        "source": args.source.strip(),
        "status": "new",
        "hypothesis_only": True,
        "production_change_authority": False,
    }
    _append(payload)
    _mongo_copy(payload)
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    print("AGY_FINDING_JSON " + encoded, flush=True)
    print("CHATGPT_HANDOFF_JSON " + encoded, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
