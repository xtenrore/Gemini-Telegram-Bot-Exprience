"""Verify the production MongoDB Atlas connection without exposing credentials."""
from __future__ import annotations

import os
import sys
from urllib.parse import urlsplit

from pymongo import MongoClient


def safe_target(uri: str) -> str:
    try:
        parsed = urlsplit(uri)
        return f"{parsed.scheme or 'mongodb'}://{parsed.hostname or 'configured-host'}"
    except Exception:
        return "mongodb://configured-host"


def main() -> int:
    uri = os.getenv("MONGO_URI", "").strip()
    if not uri:
        print("MONGO_URI is missing.", file=sys.stderr)
        return 1
    if not uri.startswith(("mongodb://", "mongodb+srv://")):
        print("MONGO_URI does not look like a MongoDB connection string.", file=sys.stderr)
        return 1

    target = safe_target(uri)
    client = MongoClient(
        uri,
        serverSelectionTimeoutMS=12000,
        connectTimeoutMS=12000,
        maxPoolSize=2,
        appname="aircraft-alert-atlas-preflight",
    )
    try:
        result = client.admin.command("ping")
        if result.get("ok") != 1.0:
            print(f"Atlas ping returned an unexpected response for {target}.", file=sys.stderr)
            return 1
        print(f"MongoDB Atlas verified: {target} (credentials hidden).")
        return 0
    except Exception as exc:
        print(
            f"MongoDB Atlas verification failed for {target}: {type(exc).__name__}. "
            "Check Atlas Network Access, database user credentials, and MONGO_URI.",
            file=sys.stderr,
        )
        return 1
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
