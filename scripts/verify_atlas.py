"""Verify the production MongoDB Atlas connection without exposing credentials."""
from __future__ import annotations

import os
import sys
from urllib.parse import urlsplit

from pymongo import MongoClient
from pymongo.errors import OperationFailure


def safe_target(uri: str) -> str:
    try:
        parsed = urlsplit(uri)
        return f"{parsed.scheme or 'mongodb'}://{parsed.hostname or 'configured-host'}"
    except Exception:
        return "mongodb://configured-host"


def _configuration_shape(uri: str) -> str:
    """Return only non-secret URI diagnostics."""
    lowered = uri.lower()
    placeholder = any(token in lowered for token in ("<db_password>", "<password>", "<username>"))
    try:
        parsed = urlsplit(uri)
        has_username = parsed.username is not None and parsed.username != ""
        has_password = parsed.password is not None and parsed.password != ""
    except Exception:
        has_username = False
        has_password = False
    return (
        f"scheme={'srv' if uri.startswith('mongodb+srv://') else 'standard'}, "
        f"username={'present' if has_username else 'missing'}, "
        f"password={'present' if has_password else 'missing'}, "
        f"placeholder={'yes' if placeholder else 'no'}"
    )


def main() -> int:
    uri = os.getenv("MONGO_URI", "").strip()
    if not uri:
        print("MONGO_URI is missing.", file=sys.stderr)
        return 1
    if not uri.startswith(("mongodb://", "mongodb+srv://")):
        print("MONGO_URI does not look like a MongoDB connection string.", file=sys.stderr)
        return 1

    target = safe_target(uri)
    print(f"Atlas URI shape: {_configuration_shape(uri)} (credential values hidden).")
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
    except OperationFailure as exc:
        code = getattr(exc, "code", None)
        if code in {18, 8000}:
            category = "authentication rejected (database username/password)"
        elif code == 13:
            category = "authenticated user lacks required permission"
        else:
            category = "Atlas operation rejected"
        print(
            f"MongoDB Atlas verification failed for {target}: OperationFailure code={code}; {category}. "
            "Credential values remain hidden.",
            file=sys.stderr,
        )
        return 1
    except Exception as exc:
        print(
            f"MongoDB Atlas verification failed for {target}: {type(exc).__name__}. "
            "If this is a server-selection/network error, check Atlas Network Access; "
            "if it is a URI parsing error, re-copy the Drivers connection string.",
            file=sys.stderr,
        )
        return 1
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
