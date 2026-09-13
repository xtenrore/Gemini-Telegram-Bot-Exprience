"""Delete every Northflank project before a one-time clean v3.2 deployment.

This script is intentionally destructive and is only invoked by the deployment
workflow when the triggering commit message contains ``[reset-northflank]``.
It uses Northflank's delete_child_objects option so services, jobs, databases,
and other project children are removed with their project.

Secrets are read from environment variables and never printed.
"""
from __future__ import annotations

import os
import sys
import time
from typing import Any

import httpx

API_BASE = "https://api.northflank.com/v1"
TIMEOUT = 30.0


def _token() -> str:
    token = os.getenv("NF_API_TOKEN", "").strip()
    if not token:
        raise RuntimeError("NF_API_TOKEN is missing")
    return token


def _items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict) and "data" in payload:
        payload = payload["data"]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("results", "projects", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _list_projects(client: httpx.Client) -> list[dict[str, Any]]:
    response = client.get("/projects")
    response.raise_for_status()
    return _items(response.json())


def reset_all_projects() -> None:
    token = _token()
    with httpx.Client(
        base_url=API_BASE,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        timeout=TIMEOUT,
    ) as client:
        projects = _list_projects(client)
        if not projects:
            print("Northflank reset: no existing projects found.")
            return

        print(f"Northflank reset: deleting {len(projects)} existing project(s) and all child resources.")
        for project in projects:
            project_id = str(project.get("id") or "").strip()
            if not project_id:
                raise RuntimeError("Northflank returned a project without an id")
            name = str(project.get("name") or project_id)
            response = client.delete(
                f"/projects/{project_id}",
                params={"delete_child_objects": "true"},
            )
            if response.status_code not in {200, 202, 204, 404}:
                raise RuntimeError(
                    f"Could not delete Northflank project {name!r} ({response.status_code}): "
                    + response.text[:500]
                )
            print(f"Delete requested: {name} ({project_id})")

        deadline = time.time() + 300
        while time.time() < deadline:
            remaining = _list_projects(client)
            if not remaining:
                print("Northflank reset complete: project list is empty.")
                return
            names = [str(item.get("name") or item.get("id") or "unknown") for item in remaining]
            print("Waiting for Northflank deletion to finish: " + ", ".join(names))
            time.sleep(5)

        raise RuntimeError("Northflank projects did not finish deleting within 5 minutes")


if __name__ == "__main__":
    try:
        reset_all_projects()
    except Exception as exc:
        print(f"NORTHFLANK RESET ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
