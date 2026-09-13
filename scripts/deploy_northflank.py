"""Idempotently deploy Aircraft Alert to Northflank from GitHub Actions.

Secrets are read only from environment variables. This script intentionally
never logs secret values or HTTP request bodies.
"""

from __future__ import annotations

import os
import re
import sys
import time
from typing import Any
from urllib.parse import quote_plus

import httpx

API_BASE = "https://api.northflank.com/v1"
PROJECT_ID = "aircraft-alerts"
ADDON_ID = "aircraft-mongo"
SERVICE_ID = "aircraft-alerts"
REPO_URL = "https://github.com/xtenrore/Gemini-Telegram-Bot-Exprience"
REGION = os.getenv("NF_REGION", "europe-west")
REQUEST_TIMEOUT = 30.0


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Required environment variable {name} is missing")
    return value


TOKEN = _required("NF_API_TOKEN")
SECRETS = [
    TOKEN,
    os.getenv("SLACK_BOT_TOKEN", ""),
    os.getenv("SLACK_APP_TOKEN", ""),
    os.getenv("GEMINI_API_KEY", ""),
    os.getenv("ADMIN_PASSWORD", ""),
    os.getenv("OPENSKY_CREDENTIALS_JSON", ""),
    os.getenv("GROQ_API_KEY", ""),
]


def _redact(text: str) -> str:
    output = text
    for secret in sorted((s for s in SECRETS if s), key=len, reverse=True):
        output = output.replace(secret, "***")
    output = re.sub(r"\b(?:xoxb|xapp)-[A-Za-z0-9-]+\b", "***", output)
    return output


class Northflank:
    def __init__(self) -> None:
        self.client = httpx.Client(
            base_url=API_BASE,
            headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
            timeout=REQUEST_TIMEOUT,
        )

    def request(self, method: str, path: str, *, json: dict[str, Any] | None = None, allowed: tuple[int, ...] = (200, 201, 202)) -> httpx.Response:
        response = self.client.request(method, path, json=json)
        if response.status_code not in allowed:
            body = _redact(response.text[:1500])
            raise RuntimeError(f"Northflank {method} {path} failed ({response.status_code}): {body}")
        return response

    def get_optional(self, path: str) -> dict[str, Any] | None:
        response = self.client.get(path)
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise RuntimeError(f"Northflank GET {path} failed ({response.status_code}): {_redact(response.text[:1500])}")
        payload = response.json()
        return payload.get("data", payload)


def _project(nf: Northflank) -> str:
    existing = nf.get_optional(f"/projects/{PROJECT_ID}")
    if existing:
        print(f"Northflank project ready: {PROJECT_ID}")
        return str(existing.get("id", PROJECT_ID))
    data = nf.request(
        "POST",
        "/projects",
        json={"name": "Aircraft Alerts", "description": "Slack aircraft proximity alerts and ADS-B monitor", "region": REGION},
    ).json().get("data", {})
    project_id = str(data.get("id", PROJECT_ID))
    print(f"Created Northflank project: {project_id}")
    return project_id


def _addon(nf: Northflank, project_id: str) -> str:
    existing = nf.get_optional(f"/projects/{project_id}/addons/{ADDON_ID}")
    if existing:
        print(f"MongoDB addon ready/existing: {ADDON_ID}")
        return str(existing.get("id", ADDON_ID))
    last_error: Exception | None = None
    for plan in ("nf-compute-10", "nf-compute-20", "nf-compute-50"):
        payload = {
            "name": "Aircraft Mongo",
            "description": "Aircraft Alert application database",
            "type": "mongodb",
            "version": "latest",
            "billing": {"deploymentPlan": plan, "storage": 1024, "replicas": 1},
            "tlsEnabled": False,
            "externalAccessEnabled": False,
        }
        try:
            data = nf.request("POST", f"/projects/{project_id}/addons", json=payload).json().get("data", {})
            addon_id = str(data.get("id", ADDON_ID))
            print(f"Created MongoDB addon {addon_id} using {plan}")
            return addon_id
        except RuntimeError as exc:
            last_error = exc
            print(f"MongoDB plan {plan} was not accepted; trying the next sandbox-size plan.")
    raise RuntimeError(f"Unable to create MongoDB addon. Last error: {last_error}")


def _wait_for_addon(nf: Northflank, project_id: str, addon_id: str) -> dict[str, Any]:
    deadline = time.time() + 600
    last_status = ""
    while time.time() < deadline:
        data = nf.get_optional(f"/projects/{project_id}/addons/{addon_id}")
        if not data:
            raise RuntimeError("MongoDB addon disappeared during provisioning")
        status = str(data.get("status", "")).lower()
        if status != last_status:
            print(f"MongoDB status: {status or 'provisioning'}")
            last_status = status
        if status == "running":
            return data
        if status in {"failed", "error"}:
            raise RuntimeError(f"MongoDB provisioning failed with status {status}")
        time.sleep(10)
    raise RuntimeError("MongoDB addon did not become ready before deployment timeout")


def _find_key(data: Any, names: tuple[str, ...]) -> str:
    if isinstance(data, dict):
        for key, value in data.items():
            if str(key).upper() in names and isinstance(value, str) and value:
                return value
        for value in data.values():
            found = _find_key(value, names)
            if found:
                return found
    elif isinstance(data, list):
        for value in data:
            found = _find_key(value, names)
            if found:
                return found
    return ""


def _mongo_uri(nf: Northflank, project_id: str, addon_id: str) -> str:
    deadline = time.time() + 180
    credentials: dict[str, Any] = {}
    while time.time() < deadline:
        response = nf.client.get(f"/projects/{project_id}/addons/{addon_id}/credentials")
        if response.status_code == 200:
            payload = response.json()
            credentials = payload.get("data", payload)
            break
        if response.status_code not in {404, 409, 425}:
            raise RuntimeError(f"Could not read MongoDB credentials ({response.status_code}): {_redact(response.text[:1200])}")
        time.sleep(5)
    if not credentials:
        raise RuntimeError("MongoDB credentials were not ready")
    for names in (("MONGO_SRV", "MONGODB_URI", "MONGO_URI", "MONGO_URL"), ("NF_MONGO_SRV", "NF_MONGO_URI")):
        uri = _find_key(credentials, names)
        if uri.startswith("mongodb"):
            return uri
    username = _find_key(credentials, ("USERNAME", "MONGO_USERNAME", "NF_MONGO_USERNAME"))
    password = _find_key(credentials, ("PASSWORD", "MONGO_PASSWORD", "NF_MONGO_PASSWORD"))
    host = _find_key(credentials, ("HOST", "MONGO_HOST", "NF_MONGO_HOST", "INTERNAL_HOST", "INTERNALHOST"))
    port = _find_key(credentials, ("PORT", "MONGO_PORT", "NF_MONGO_PORT"))
    database = _find_key(credentials, ("DATABASE", "DB", "MONGO_DATABASE", "NF_MONGO_DATABASE")) or "aircraft_bot"
    if username and password and host:
        authority = f"{quote_plus(username)}:{quote_plus(password)}@{host}"
        if port:
            authority += f":{port}"
        return f"mongodb://{authority}/{quote_plus(database)}?authSource=admin"
    raise RuntimeError("Northflank returned MongoDB credentials but no recognized connection string fields. Top-level keys: " + str(sorted(str(k) for k in credentials.keys())))


def _runtime_env(mongo_uri: str) -> dict[str, str]:
    env = {
        "MONGO_URI": mongo_uri,
        "DATABASE_NAME": "aircraft_bot",
        "SLACK_BOT_TOKEN": _required("SLACK_BOT_TOKEN"),
        "SLACK_APP_TOKEN": _required("SLACK_APP_TOKEN"),
        "GEMINI_API_KEY": _required("GEMINI_API_KEY"),
        "GEMINI_MODEL_PRIMARY": "gemini-3.5-flash-lite",
        "GEMINI_MODEL_SECONDARY": "gemini-3.1-flash-lite",
        "POLL_INTERVAL_SECONDS": "5",
        "DEFAULT_RADIUS_KM": "15.0",
        "COOLDOWN_MINUTES": "30",
        "LEARNING_PLANE_THRESHOLD": "100",
        "RELEARN_PLANE_COUNT": "25",
        "LOG_LEVEL": "INFO",
        "PORT": "8000",
        "HOST": "0.0.0.0",
    }
    for key in ("SLACK_ALERT_CHANNEL_ID", "ADMIN_SLACK_USER_ID", "ADMIN_PASSWORD", "OPENSKY_CREDENTIALS_JSON", "GROQ_API_KEY"):
        value = os.getenv(key, "").strip()
        if value:
            env[key] = value
    return env


def _service_payload(runtime_env: dict[str, str]) -> dict[str, Any]:
    return {
        "name": "Aircraft Alerts",
        "description": "Slack Socket Mode aircraft alert service and 5-second ADS-B worker",
        "billing": {"deploymentPlan": "nf-compute-10"},
        "deployment": {"instances": 1, "docker": {"configType": "default"}},
        "ports": [{"name": "http", "internalPort": 8000, "public": True, "protocol": "HTTP"}],
        "disabledCI": True,
        "buildSource": "git",
        "vcsData": {"projectUrl": REPO_URL, "projectType": "github", "projectBranch": "main"},
        "buildSettings": {"dockerfile": {"buildEngine": "buildkit", "dockerFilePath": "/Dockerfile", "dockerWorkDir": "/"}},
        "runtimeEnvironment": runtime_env,
    }


def _service(nf: Northflank, project_id: str, runtime_env: dict[str, str]) -> str:
    existing = nf.get_optional(f"/projects/{project_id}/services/{SERVICE_ID}")
    payload = _service_payload(runtime_env)
    if existing:
        service_id = str(existing.get("id", SERVICE_ID))
        nf.request("PATCH", f"/projects/{project_id}/services/combined/{service_id}", json=payload)
        print(f"Updated Northflank service: {service_id}")
        return service_id
    data = nf.request("POST", f"/projects/{project_id}/services/combined", json=payload).json().get("data", {})
    service_id = str(data.get("id", SERVICE_ID))
    print(f"Created Northflank service: {service_id}")
    return service_id


def _trigger_build(nf: Northflank, project_id: str, service_id: str) -> None:
    sha = os.getenv("GITHUB_SHA", "").strip()
    payload: dict[str, str] = {}
    if re.fullmatch(r"[0-9a-fA-F]{40}", sha):
        payload["sha"] = sha
    nf.request("POST", f"/projects/{project_id}/services/{service_id}/build", json=payload, allowed=(200, 201, 202, 204))
    print("Northflank build triggered.")


def _wait_for_service(nf: Northflank, project_id: str, service_id: str) -> dict[str, Any]:
    deadline = time.time() + 900
    last = ""
    while time.time() < deadline:
        data = nf.get_optional(f"/projects/{project_id}/services/{service_id}")
        if not data:
            raise RuntimeError("Northflank service disappeared")
        status = data.get("status", {})
        build = str(status.get("build", {}).get("status", "")).upper() if isinstance(status, dict) else ""
        deployment = str(status.get("deployment", {}).get("status", "")).upper() if isinstance(status, dict) else ""
        summary = f"build={build or '?'} deployment={deployment or '?'}"
        if summary != last:
            print(f"Service status: {summary}")
            last = summary
        if build in {"FAILED", "FAILURE", "CRASHED", "SUBMISSION_FAILURE"}:
            raise RuntimeError(f"Northflank build failed: {build}")
        if deployment in {"FAILED", "FAILURE", "CRASHED"}:
            raise RuntimeError(f"Northflank deployment failed: {deployment}")
        if build in {"SUCCESS", "SUCCEEDED", "COMPLETED"} and deployment in {"COMPLETED", "SUCCESS", "RUNNING"}:
            return data
        time.sleep(10)
    raise RuntimeError("Northflank service did not become ready before deployment timeout")


def _public_url(nf: Northflank, project_id: str, service_id: str) -> str:
    response = nf.client.get(f"/projects/{project_id}/services/{service_id}/ports")
    if response.status_code != 200:
        return ""
    payload = response.json()
    data = payload.get("data", payload)

    def walk(value: Any) -> str:
        if isinstance(value, dict):
            for key, item in value.items():
                k = str(key).lower()
                if isinstance(item, str) and item:
                    if k in {"dns", "fqdn", "hostname"} and "." in item:
                        return item if item.startswith("http") else f"https://{item}"
                    if k in {"url", "endpoint"} and item.startswith("http"):
                        return item
            for item in value.values():
                found = walk(item)
                if found:
                    return found
        elif isinstance(value, list):
            for item in value:
                found = walk(item)
                if found:
                    return found
        return ""

    return walk(data)


def main() -> int:
    print("Deploying Aircraft Alert v3.1 to Northflank...")
    nf = Northflank()
    project_id = _project(nf)
    addon_id = _addon(nf, project_id)
    _wait_for_addon(nf, project_id, addon_id)
    mongo_uri = _mongo_uri(nf, project_id, addon_id)
    print("MongoDB credentials resolved (value hidden).")
    service_id = _service(nf, project_id, _runtime_env(mongo_uri))
    _trigger_build(nf, project_id, service_id)
    _wait_for_service(nf, project_id, service_id)
    url = _public_url(nf, project_id, service_id)
    print("Northflank deployment is running.")
    if url:
        print(f"Public URL: {url}")
        try:
            response = httpx.get(f"{url.rstrip('/')}/health", timeout=15)
            print(f"Health endpoint HTTP {response.status_code}")
            if response.status_code != 200:
                return 1
        except Exception as exc:
            print(f"Health endpoint could not be verified yet: {_redact(str(exc))}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"DEPLOYMENT ERROR: {_redact(str(exc))}", file=sys.stderr)
        raise SystemExit(1)
