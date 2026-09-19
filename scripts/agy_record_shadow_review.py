#!/usr/bin/env python3
"""Persist a non-authoritative AGY shadow opinion to the isolated volume."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

STATE_DIR = Path(os.getenv("AGY_STATE_DIR", "/agy-state"))
REVIEWS_FILE = STATE_DIR / "prediction-lab" / "shadow-reviews.jsonl"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--decision-id", required=True)
    parser.add_argument("--opinion", required=True, choices=["allow", "suppress", "wait", "cancel"])
    parser.add_argument("--confidence", default="medium", choices=["low", "medium", "high"])
    parser.add_argument("--evidence", required=True)
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    payload = {
        "decision_id": args.decision_id.strip(),
        "opinion": args.opinion,
        "confidence": args.confidence,
        "evidence": args.evidence.strip(),
        "reviewed_at": now.isoformat(),
        "expires_at": (now + timedelta(days=14)).isoformat(),
        "authoritative": False,
        "affects_live_decision": False,
        "schema": "plane-alerts-agy-shadow-v4.4",
    }
    REVIEWS_FILE.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
    with REVIEWS_FILE.open("a", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())
    print("AGY_SHADOW_REVIEW_JSON " + line.strip(), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
