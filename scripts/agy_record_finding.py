#!/usr/bin/env python3
"""Persist an Antigravity finding immediately.

The AGY goal is instructed to invoke this tool as soon as a confirmed issue is
found rather than waiting for the whole run to finish.  Each finding is:
1. appended atomically to the Railway volume JSONL inbox,
2. emitted to stdout as AGY_FINDING_JSON so Railway logs are an external handoff,
3. inserted into MongoDB when MONGO_URI is available.

This makes findings visible even when ChatGPT's scheduled review begins halfway
through an Antigravity run.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

STATE_DIR = Path(os.getenv("AGY_STATE_DIR", "/agy-state"))
INBOX = STATE_DIR / "prediction-lab" / "findings.jsonl"
SEQ_FILE = STATE_DIR / "prediction-lab" / "finding-seq.txt"


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


def _append(payload: dict) -> None:
    INBOX.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
    with INBOX.open("a", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())


def _mongo_copy(payload: dict) -> None:
    uri = os.getenv("MONGO_URI", "").strip()
    if not uri:
        return
    try:
        from pymongo import MongoClient

        client = MongoClient(uri, serverSelectionTimeoutMS=4000, connectTimeoutMS=4000)
        db_name = os.getenv("DATABASE_NAME", "aircraft_bot")
        client[db_name]["agy_findings"].update_one(
            {"finding_id": payload["finding_id"]},
            {"$setOnInsert": payload},
            upsert=True,
        )
        client.close()
    except Exception as exc:  # Durable volume + logs remain authoritative fallback.
        print(f"AGY_FINDING_MONGO_WARN {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--severity", required=True, choices=["info", "low", "medium", "high", "critical"])
    parser.add_argument("--summary", required=True)
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--suggested-fix", default="")
    parser.add_argument("--flight", default="")
    parser.add_argument("--category", default="prediction")
    parser.add_argument("--source", default="antigravity")
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    payload = {
        "seq": _next_seq(),
        "finding_id": f"agy-{now.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}",
        "created_at": now.isoformat(),
        "created_at_epoch": time.time(),
        "severity": args.severity,
        "summary": args.summary.strip(),
        "evidence": args.evidence.strip(),
        "suggested_fix": args.suggested_fix.strip(),
        "flight": args.flight.strip(),
        "category": args.category.strip(),
        "source": args.source.strip(),
        "status": "new",
    }
    _append(payload)
    _mongo_copy(payload)
    print("AGY_FINDING_JSON " + json.dumps(payload, ensure_ascii=False, separators=(",", ":")), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
