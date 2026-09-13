"""Provision/update the production Northflank stack from GitHub Actions.

Slack credentials are optional during infrastructure provisioning. When
SLACK_BOT_TOKEN and SLACK_APP_TOKEN are added later, rerunning this workflow
updates the same service and enables Socket Mode.
"""

from __future__ import annotations

import os
import re
import sys
import time
from typing import Any
from urllib.parse import quote_plus

import httpx

BASE = "https://api.northflank.com/v1"
PROJECT = "aircraft-alerts"
ADDON = "aircraft-mongo"
SERVICE = "aircraft-alerts"
REPO = "https://github.com/xtenrore/Gemini-Telegram-Bot-Exprience"
REGION = os.getenv("NF_REGION", "europe-west")


def required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Required environment variable {name} is missing")
    return value


TOKEN = required("NF_API_TOKEN")
KNOWN_SECRETS = [
    TOKEN,
    os.getenv("SLACK_BOT_TOKEN", ""),
    os.getenv("SLACK_APP_TOKEN", ""),
    os.getenv("GEMINI_API_KEY", ""),
    os.getenv("ADMIN_PASSWORD", ""),
    os.getenv("OPENSKY_CREDENTIALS_JSON", ""),
    os.getenv("GROQ_API_KEY", ""),
]


def redact(text: str) -> str:
    out = text
    for secret in sorted((x for x in KNOWN_SECRETS if x), key=len, reverse=True):
        out = out.replace(secret, "***")
    return re.sub(r"\b(?:xoxb|xapp)-[A-Za-z0-9-]+\b", "***", out)


class NF:
    def __init__(self) -> None:
        self.http = httpx.Client(
            base_url=BASE,
            headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
            timeout=30,
        )

    def call(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        ok: tuple[int, ...] = (200, 201, 202),
    ) -> dict[str, Any]:
        response = self.http.request(method, path, json=body)
        if response.status_code not in ok:
            raise RuntimeError(
                f"Northflank {method} {path} failed ({response.status_code}): "
                f"{redact(response.text[:1200])}"
            )
        if response.status_code == 204 or not response.content:
            return {}
        payload = response.json()
        return payload.get("data", payload)

    def get(self, path: str) -> dict[str, Any] | None:
        response = self.http.get(path)
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise RuntimeError(
                f"Northflank GET {path} failed ({response.status_code}): "
                f"{redact(response.text[:1200])}"
            )
        payload = response.json()
        return payload.get("data", payload)


def ensure_project(nf: NF) -> str:
    current = nf.get(f"/projects/{PROJECT}")
    if current:
        print("Northflank project exists.")
        return str(current.get("id", PROJECT))
    created = nf.call(
        "POST",
        "/projects",
        {
            "name": "Aircraft Alerts",
            "description": "Slack aircraft proximity alerts and ADS-B monitor",
            "region": REGION,
        },
    )
    project_id = str(created.get("id", PROJECT))
    print(f"Created Northflank project: {project_id}")
    return project_id


def ensure_addon(nf: NF, project_id: str) -> str:
    current = nf.get(f"/projects/{project_id}/addons/{ADDON}")
    if current:
        print("MongoDB addon exists.")
        return str(current.get("id", ADDON))

    errors: list[str] = []
    for plan in ("nf-compute-10", "nf-compute-20", "nf-compute-50"):
        try:
            created = nf.call(
                "POST",
                f"/projects/{project_id}/addons",
                {
                    "name": "Aircraft Mongo",
                    "description": "Aircraft Alert application database",
                    "type": "mongodb",
                    "version": "latest",
                    "billing": {
                        "deploymentPlan": plan,
                        "storage": 1024,
                        "replicas": 1,
                    },
                    "tlsEnabled": False,
                    "externalAccessEnabled": False,
                },
            )
            addon_id = str(created.get("id", ADDON))
            print(f"Created MongoDB addon using {plan}: {addon_id}")
            return addon_id
        except RuntimeError as exc:
            errors.append(str(exc))
            print(f"MongoDB plan {plan} unavailable; trying next small plan.")
    raise RuntimeError("Could not create MongoDB addon. " + errors[-1])


def wait_addon(nf: NF, project_id: str, addon_id: str) -> None:
    deadline = time.time() + 600
    last = ""
    while time.time() < deadline:
        addon = nf.get(f"/projects/{project_id}/addons/{addon_id}") or {}
        status = str(addon.get("status", "")).lower()
        if status != last:
            print(f"MongoDB status: {status or 'provisioning'}")
            last = status
        if status == "running":
            return
        if status in {"failed", "error"}:
            raise RuntimeError(f"MongoDB provisioning failed: {status}")
        time.sleep(10)
    raise RuntimeError("MongoDB provisioning timeout")


def deep_find(value: Any, wanted: set[str]) -> str:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).upper() in wanted and isinstance(item, str) and item:
                return item
        for item in value.values():
            found = deep_find(item, wanted)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = deep_find(item, wanted)
            if found:
                return found
    return ""


def mongo_uri(nf: NF, project_id: str, addon_id: str) -> str:
    deadline = time.time() + 180
    credentials: dict[str, Any] | None = None
    while time.time() < deadline:
        response = nf.http.get(f"/projects/{project_id}/addons/{addon_id}/credentials")
        if response.status_code == 200:
            payload = response.json()
            credentials = payload.get("data", payload)
            break
        if response.status_code not in {404, 409, 425}:
            raise RuntimeError(
                f"MongoDB credentials request failed ({response.status_code}): "
                f"{redact(response.text[:1000])}"
            )
        time.sleep(5)
    if not credentials:
        raise RuntimeError("MongoDB credentials were not ready")

    uri = deep_find(
        credentials,
        {"MONGO_SRV", "MONGO_URI", "MONGODB_URI", "MONGO_URL", "NF_MONGO_SRV", "NF_MONGO_URI"},
    )
    if uri.startswith("mongodb"):
        return uri

    user = deep_find(credentials, {"USERNAME", "MONGO_USERNAME", "NF_MONGO_USERNAME"})
    password = deep_find(credentials, {"PASSWORD", "MONGO_PASSWORD", "NF_MONGO_PASSWORD"})
    host = deep_find(credentials, {"HOST", "MONGO_HOST", "NF_MONGO_HOST", "INTERNAL_HOST"})
    port = deep_find(credentials, {"PORT", "MONGO_PORT", "NF_MONGO_PORT"})
    database = deep_find(credentials, {"DATABASE", "DB", "MONGO_DATABASE"}) or "aircraft_bot"
    if user and password and host:
        authority = f"{quote_plus(user)}:{quote_plus(password)}@{host}"
        if port:
            authority += f":{port}"
        return f"mongodb://{authority}/{quote_plus(database)}?authSource=admin"
    raise RuntimeError(
        "MongoDB credentials returned an unfamiliar shape. Keys: "
        + ", ".join(sorted(str(x) for x in credentials.keys()))
    )


def environment(uri: str) -> dict[str, str]:
    env = {
        "MONGO_URI": uri,
        "DATABASE_NAME": "aircraft_bot",
        "SLACK_BOT_TOKEN": os.getenv("SLACK_BOT_TOKEN", "").strip(),
        "SLACK_APP_TOKEN": os.getenv("SLACK_APP_TOKEN", "").strip(),
        "GEMINI_API_KEY": required("GEMINI_API_KEY"),
        "GEMINI_MODEL_PRIMARY": "gemini-3.5-flash-lite",
        "GEMINI_MODEL_SECONDARY": "gemini-3.1-flash-lite",
        "POLL_INTERVAL_SECONDS": "5",
        "DEFAULT_RADIUS_KM": "15.0",
        "COOLDOWN_MINUTES": "30",
        "LEARNING_PLANE_THRESHOLD": "100",
        "RELEARN_PLANE_COUNT": "25",
        "HOST": "0.0.0.0",
        "PORT": "8000",
        "LOG_LEVEL": "INFO",
    }
    for key in (
        "SLACK_ALERT_CHANNEL_ID",
        "ADMIN_SLACK_USER_ID",
        "ADMIN_PASSWORD",
        "OPENSKY_CREDENTIALS_JSON",
        "GROQ_API_KEY",
    ):
        if os.getenv(key, "").strip():
            env[key] = os.environ[key].strip()
    return env


def service_body(env: dict[str, str]) -> dict[str, Any]:
    return {
        "name": "Aircraft Alerts",
        "description": "Slack Socket Mode aircraft alert service and 5-second ADS-B worker",
        "billing": {"deploymentPlan": "nf-compute-10"},
        "deployment": {"instances": 1, "docker": {"configType": "default"}},
        "ports": [
            {"name": "http", "internalPort": 8000, "public": True, "protocol": "HTTP"}
        ],
        "disabledCI": True,
        "buildSource": "git",
        "vcsData": {
            "projectUrl": REPO,
            "projectType": "github",
            "projectBranch": "main",
        },
        "buildSettings": {
            "dockerfile": {
                "buildEngine": "buildkit",
                "dockerFilePath": "/Dockerfile",
                "dockerWorkDir": "/",
            }
        },
        "runtimeEnvironment": env,
    }


def ensure_service(nf: NF, project_id: str, env: dict[str, str]) -> str:
    current = nf.get(f"/projects/{project_id}/services/{SERVICE}")
    body = service_body(env)
    if current:
        service_id = str(current.get("id", SERVICE))
        nf.call("PATCH", f"/projects/{project_id}/services/combined/{service_id}", body)
        print("Updated Northflank service.")
        return service_id
    created = nf.call("POST", f"/projects/{project_id}/services/combined", body)
    service_id = str(created.get("id", SERVICE))
    print(f"Created Northflank service: {service_id}")
    return service_id


def deploy_service(nf: NF, project_id: str, service_id: str) -> None:
    sha = os.getenv("GITHUB_SHA", "")
    body = {"sha": sha} if re.fullmatch(r"[0-9a-fA-F]{40}", sha) else {}
    nf.call(
        "POST",
        f"/projects/{project_id}/services/{service_id}/build",
        body,
        ok=(200, 201, 202, 204),
    )
    print("Northflank build triggered.")

    deadline = time.time() + 900
    last = ""
    while time.time() < deadline:
        service = nf.get(f"/projects/{project_id}/services/{service_id}") or {}
        status = service.get("status", {})
        build = (
            str((status.get("build") or {}).get("status", "")).upper()
            if isinstance(status, dict)
            else ""
        )
        deployment = (
            str((status.get("deployment") or {}).get("status", "")).upper()
            if isinstance(status, dict)
            else ""
        )
        summary = f"build={build or '?'} deployment={deployment or '?'}"
        if summary != last:
            print(f"Service status: {summary}")
            last = summary
        if build in {"FAILED", "FAILURE", "CRASHED", "SUBMISSION_FAILURE"}:
            raise RuntimeError(f"Northflank build failed: {build}")
        if deployment in {"FAILED", "FAILURE", "CRASHED"}:
            raise RuntimeError(f"Northflank deployment failed: {deployment}")
        if build in {"SUCCESS", "SUCCEEDED", "COMPLETED"} and deployment in {
            "COMPLETED",
            "SUCCESS",
            "RUNNING",
        }:
            return
        time.sleep(10)
    raise RuntimeError("Northflank service deployment timeout")


def main() -> int:
    slack_ready = bool(
        os.getenv("SLACK_BOT_TOKEN", "").strip() and os.getenv("SLACK_APP_TOKEN", "").strip()
    )
    print(
        "Deploying Aircraft Alert v3.1 to Northflank "
        f"(Slack credentials {'present' if slack_ready else 'not yet present'})..."
    )
    nf = NF()
    project_id = ensure_project(nf)
    addon_id = ensure_addon(nf, project_id)
    wait_addon(nf, project_id, addon_id)
    uri = mongo_uri(nf, project_id, addon_id)
    print("MongoDB connection resolved (value hidden).")
    service_id = ensure_service(nf, project_id, environment(uri))
    deploy_service(nf, project_id, service_id)
    print("Northflank infrastructure and application service are deployed.")
    if not slack_ready:
        print(
            "Slack Socket Mode is intentionally inactive until GitHub secrets "
            "SLACK_BOT_TOKEN and SLACK_APP_TOKEN are added; rerun this workflow afterwards."
        )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"DEPLOYMENT ERROR: {redact(str(exc))}", file=sys.stderr)
        raise SystemExit(1)
