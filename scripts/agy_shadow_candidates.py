#!/usr/bin/env python3
"""Print recent deterministic ambiguity cases for AGY shadow review.

Read-only by design. AGY may inspect these cases, but nothing here participates
in live alert decisions.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone


def main() -> int:
    uri = os.getenv("MONGO_URI", "").strip()
    if not uri:
        print(json.dumps({"candidates": [], "error": "MONGO_URI unavailable"}))
        return 0
    try:
        from pymongo import MongoClient

        client = MongoClient(uri, serverSelectionTimeoutMS=4000, connectTimeoutMS=4000)
        db = client[os.getenv("DATABASE_NAME", "aircraft_bot")]
        cutoff = datetime.now(timezone.utc) - timedelta(hours=4)
        reviewed = {doc["decision_id"] for doc in db["agy_shadow_reviews"].find({}, {"decision_id": 1}).limit(500)}
        query = {
            "subsystem": "prediction",
            "timestamp": {"$gte": cutoff},
            "$or": [
                {"decision_reason_code": {"$in": ["terminal_arrival_transient_alignment", "active_turn_transient_alignment"]}},
                {"evidence.ensemble_pass_score": {"$gt": 0.30, "$lt": 0.85}},
                {"evidence.prediction_confidence": {"$in": ["Low", "Uncertain"]}},
            ],
        }
        out = []
        for doc in db["decision_records"].find(query).sort("timestamp", -1).limit(60):
            decision_id = str(doc.get("decision_id") or "")
            if not decision_id or decision_id in reviewed:
                continue
            evidence = dict(doc.get("evidence") or {})
            out.append({
                "decision_id": decision_id,
                "timestamp": doc.get("timestamp").isoformat() if hasattr(doc.get("timestamp"), "isoformat") else str(doc.get("timestamp") or ""),
                "deterministic_decision": doc.get("decision"),
                "reason_code": doc.get("decision_reason_code"),
                "state": doc.get("new_state"),
                "evidence": evidence,
            })
            if len(out) >= 20:
                break
        client.close()
        print(json.dumps({"candidates": out}, default=str, separators=(",", ":")))
        return 0
    except Exception as exc:
        print(json.dumps({"candidates": [], "error": f"{type(exc).__name__}: {exc}"}, separators=(",", ":")))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
