#!/usr/bin/env python3
"""Persist a non-authoritative AGY opinion for one deterministic decision."""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--decision-id", required=True)
    parser.add_argument("--opinion", required=True, choices=["allow", "suppress", "wait", "cancel"])
    parser.add_argument("--confidence", default="medium", choices=["low", "medium", "high"])
    parser.add_argument("--evidence", required=True)
    args = parser.parse_args()

    payload = {
        "decision_id": args.decision_id.strip(),
        "opinion": args.opinion,
        "confidence": args.confidence,
        "evidence": args.evidence.strip(),
        "reviewed_at": datetime.now(timezone.utc),
        "expires_at": datetime.now(timezone.utc) + timedelta(days=14),
        "authoritative": False,
        "affects_live_decision": False,
        "schema": "plane-alerts-agy-shadow-v4.4",
    }
    uri = os.getenv("MONGO_URI", "").strip()
    if uri:
        try:
            from pymongo import MongoClient

            client = MongoClient(uri, serverSelectionTimeoutMS=4000, connectTimeoutMS=4000)
            db = client[os.getenv("DATABASE_NAME", "aircraft_bot")]
            exists = db["decision_records"].find_one({"decision_id": payload["decision_id"]}, {"_id": 1})
            if not exists:
                raise SystemExit("decision_id does not exist")
            db["agy_shadow_reviews"].update_one(
                {"decision_id": payload["decision_id"]},
                {"$set": payload},
                upsert=True,
            )
            client.close()
        except Exception as exc:
            raise SystemExit(f"shadow review persistence failed: {type(exc).__name__}: {exc}") from exc

    printable = dict(payload)
    printable["reviewed_at"] = payload["reviewed_at"].isoformat()
    printable["expires_at"] = payload["expires_at"].isoformat()
    print("AGY_SHADOW_REVIEW_JSON " + json.dumps(printable, separators=(",", ":")), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
