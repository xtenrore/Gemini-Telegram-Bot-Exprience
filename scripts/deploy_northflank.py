"""Idempotently deploy Aircraft Alert v3.2 to Northflank from GitHub Actions.

The deployment uses the Developer Sandbox shape intentionally: one MongoDB
addon plus two small always-on services (web/Telegram and ADS-B worker). Secret
values are never printed or embedded in repository files.
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
PROJECT_HINT = "aircraft-alerts"
ADDON_HINT = "aircraft-mongo"
WEB_HINT = "aircraft-web"
WORKER_HINT = "aircraft-worker"
REPO_URL = "https://github.com/xtenrore/Gemini-Telegram-Bot-Exprience"
REGION = os.getenv("NF_REGION", "europe-west")
REQUEST_TIMEOUT = 30.0


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Required environment variable {name} is missing")
    return value


TOKEN = _required("NF_API_TOKEN")
SECRET_VALUES = [
    TOKEN,
    os.getenv("TELEGRAM_BOT_TOKEN", ""),
    os.getenv("GEMINI_API_KEY", ""),
    os.getenv("ADMIN_PASSWORD", ""),
    os.getenv("OPENSKY_CREDENTIALS_JSON", ""),
    os.getenv("GROQ_API_KEY", ""),
    os.getenv("WEBHOOK_SECRET", ""),
]


def _redact(text: str) -> str:
    output = text
    for secret in sorted((s for s in SECRET_VALUES if s), key=len, reverse=True):
        output = output.replace(secret, "***")
    output = re.sub(r"\b\d{7,12}:[A-Za-z0-9_-]{20,}\b", "***", output)
    return output


class Northflank:
    def __init__(self) -> None:
        self.client = httpx.Client(
            base_url=API_BASE,
            headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
            timeout=REQUEST_TIMEOUT,
        )

    def request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        allowed: tuple[int, ...] = (200, 201, 202),
    ) -> httpx.Response:
        response = self.client.request(method, path, json=json)
        if response.status_code not in allowed:
            raise RuntimeError(
                f"Northflank {method} {path} failed ({response.status_code}): "
                + _redact(response.text[:1800])
            )
        return response

    def get_optional(self, path: str) -> dict[str, Any] | None:
        response = self.client.get(path)
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise RuntimeError(
                f"Northflank GET {path} failed ({response.status_code}): "
                + _redact(response.text[:1800])
            )
        payload = response.json()
        return payload.get("data", payload)


def _get_by_hint(nf: Northflank, path: str, hint: str) -> dict[str, Any] | None:
    """Try a predictable slug first, then locate a named object from list output."""
    direct = nf.get_optional(f"{path}/{hint}")
    if direct:
        return direct
    response = nf.client.get(path)
    if response.status_code != 200:
        return None
    payload = response.json().get("data", response.json())
    items = payload if isinstance(payload, list) else payload.get("results", []) if isinstance(payload, dict) else []
    normalized = hint.replace("-", " ").lower()
    for item in items:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id", ""))
        name = str(item.get("name", "")).lower()
        if item_id == hint or name == normalized or name.replace(" ", "-") == hint:
            return item
    return None


def _project(nf: Northflank) -> str:
    existing = nf.get_optional(f"/projects/{PROJECT_HINT}")
    if existing:
        project_id = str(existing.get("id", PROJECT_HINT))
        print(f"Northflank project ready: {project_id}")
        return project_id

    response = nf.client.get("/projects")
    if response.status_code == 200:
        data = response.json().get("data", response.json())
        items = data if isinstance(data, list) else data.get("results", []) if isinstance(data, dict) else []
        for item in items:
            if isinstance(item, dict) and str(item.get("name", "")).lower() == "aircraft alerts":
                project_id = str(item.get("id"))
                print(f"Northflank project ready: {project_id}")
                return project_id

    data = nf.request(
        "POST",
        "/projects",
        json={
            "name": "Aircraft Alerts",
            "description": "Telegram ADS-B alerts with Gemini aviation photography guidance",
            "region": REGION,
        },
    ).json().get("data", {})
    project_id = str(data.get("id") or PROJECT_HINT)
    print(f"Created Northflank project: {project_id}")
    return project_id


def _addon(nf: Northflank, project_id: str) -> str:
    existing = _get_by_hint(nf, f"/projects/{project_id}/addons", ADDON_HINT)
    if existing:
        addon_id = str(existing.get("id", ADDON_HINT))
        print(f"MongoDB addon ready/existing: {addon_id}")
        return addon_id

    last_error: Exception | None = None
    for plan, storage in (("nf-compute-10", 1024), ("nf-compute-20", 1024), ("nf-compute-20", 6144)):
        payload = {
            "name": "Aircraft Mongo",
            "description": "Aircraft Alert v3.2 application database",
            "type": "mongodb",
            "version": "latest",
            "billing": {"deploymentPlan": plan, "storage": storage, "replicas": 1},
            "tlsEnabled": False,
            "externalAccessEnabled": False,
        }
        try:
            data = nf.request("POST", f"/projects/{project_id}/addons", json=payload).json().get("data", {})
            addon_id = str(data.get("id") or ADDON_HINT)
            print(f"Created MongoDB addon: {addon_id}")
            return addon_id
        except RuntimeError as exc:
            last_error = exc
            print(f"MongoDB sandbox plan {plan}/{storage} MiB not accepted; trying fallback.")
    raise RuntimeError(f"Unable to create MongoDB addon. Last error: {last_error}")


def _wait_for_addon(nf: Northflank, project_id: str, addon_id: str) -> None:
    deadline = time.time() + 600
    last = ""
    while time.time() < deadline:
        data = nf.get_optional(f"/projects/{project_id}/addons/{addon_id}")
        if not data:
            raise RuntimeError("MongoDB addon disappeared during provisioning")
        status = str(data.get("status", "")).lower()
        if status != last:
            print(f"MongoDB status: {status or 'provisioning'}")
            last = status
        if status == "running":
            return
        if status in {"failed", "error"}:
            raise RuntimeError(f"MongoDB provisioning failed with status {status}")
        time.sleep(8)
    raise RuntimeError("MongoDB addon did not become ready before timeout")


def _find_key(data: Any, names: tuple[str, ...]) -> str:
    wanted = {name.upper() for name in names}
    if isinstance(data, dict):
        for key, value in data.items():
            if str(key).upper() in wanted and isinstance(value, str) and value:
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
    paths = [
        f"/projects/{project_id}/addons/{addon_id}/credentials",
        f"/projects/{project_id}/addons/{addon_id}/secrets",
    ]
    while time.time() < deadline:
        for path in paths:
            response = nf.client.get(path)
            if response.status_code == 200:
                payload = response.json()
                credentials = payload.get("data", payload)
                break
            if response.status_code not in {404, 409, 425}:
                raise RuntimeError(
                    f"Could not read MongoDB credentials ({response.status_code}): "
                    + _redact(response.text[:1200])
                )
        if credentials:
            break
        time.sleep(5)
    if not credentials:
        raise RuntimeError("MongoDB credentials were not ready")

    uri = _find_key(
        credentials,
        ("MONGO_SRV", "MONGODB_URI", "MONGO_URI", "MONGO_URL", "NF_MONGO_SRV", "NF_MONGO_URI"),
    )
    if uri.startswith("mongodb"):
        return uri

    username = _find_key(credentials, ("USERNAME", "MONGO_USERNAME", "NF_MONGO_USERNAME"))
    password = _find_key(credentials, ("PASSWORD", "MONGO_PASSWORD", "NF_MONGO_PASSWORD"))
    host = _find_key(credentials, ("HOST", "MONGO_HOST", "NF_MONGO_HOST", "INTERNAL_HOST"))
    port = _find_key(credentials, ("PORT", "MONGO_PORT", "NF_MONGO_PORT"))
    database = _find_key(credentials, ("DATABASE", "DB", "MONGO_DATABASE", "NF_MONGO_DATABASE")) or "aircraft_bot"
    if username and password and host:
        authority = f"{quote_plus(username)}:{quote_plus(password)}@{host}"
        if port:
            authority += f":{port}"
        return f"mongodb://{authority}/{quote_plus(database)}?authSource=admin"
    raise RuntimeError("Northflank returned MongoDB credentials but no recognized connection string")


def _runtime_env(mongo_uri: str) -> dict[str, str]:
    env = {
        "MONGO_URI": mongo_uri,
        "DATABASE_NAME": "aircraft_bot",
        "TELEGRAM_BOT_TOKEN": _required("TELEGRAM_BOT_TOKEN"),
        "GEMINI_API_KEY": _required("GEMINI_API_KEY"),
        "GEMINI_MODEL_PRIMARY": "gemini-3.5-flash-lite",
        "GEMINI_MODEL_SECONDARY": "gemini-3.5-flash",
        "GEMINI_PHOTO_MODEL": "gemini-3.8-flash",
        "GEMINI_PHOTO_FALLBACK_MODEL": "gemini-3.5-flash-lite",
        "GEMINI_PHOTO_TIMEOUT_SECONDS": "30",
        "OPEN_METEO_FORECAST_URL": "https://api.open-meteo.com/v1/forecast",
        "OPEN_METEO_AIR_QUALITY_URL": "https://air-quality-api.open-meteo.com/v1/air-quality",
        "PHOTOGRAPHY_HTTP_TIMEOUT_SECONDS": "12",
        "PHOTOGRAPHY_CONDITIONS_CACHE_SECONDS": "120",
        "POLL_INTERVAL_SECONDS": "5",
        "DEFAULT_RADIUS_KM": "15.0",
        "COOLDOWN_MINUTES": "30",
        "LEARNING_PLANE_THRESHOLD": "100",
        "RELEARN_PLANE_COUNT": "25",
        "LOG_LEVEL": "INFO",
        "PORT": "8000",
        "HOST": "0.0.0.0",
        "WEBHOOK_URL": "",
    }
    for key in (
        "ADMIN_TELEGRAM_ID",
        "ADMIN_PASSWORD",
        "OPENSKY_CREDENTIALS_JSON",
        "GROQ_API_KEY",
    ):
        value = os.getenv(key, "").strip()
        if value:
            env[key] = value
    return env


def _service_payload(
    *,
    name: str,
    description: str,
    command: str,
    runtime_env: dict[str, str],
    public_http: bool,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": name,
        "description": description,
        "billing": {
            "deploymentPlan": "nf-compute-10",
            "buildPlan": "nf-compute-400-16",
        },
        "deployment": {
            "instances": 1,
            "docker": {"configType": "customCommand", "customCommand": command},
        },
        "disabledCI": True,
        "buildSource": "git",
        "vcsData": {
            "projectUrl": REPO_URL,
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
        "runtimeEnvironment": runtime_env,
    }
    if public_http:
        payload["ports"] = [
            {"name": "http", "internalPort": 8000, "public": True, "protocol": "HTTP"}
        ]
        payload["healthChecks"] = [
            {
                "protocol": "HTTP",
                "type": "readinessProbe",
                "path": "/health",
                "port": 8000,
                "initialDelaySeconds": 10,
                "periodSeconds": 20,
                "timeoutSeconds": 5,
                "failureThreshold": 6,
                "successThreshold": 1,
            }
        ]
    else:
        payload["healthChecks"] = [
            {
                "protocol": "CMD",
                "type": "livenessProbe",
                "cmd": "python -c \"import os; os.kill(1, 0)\"",
                "initialDelaySeconds": 10,
                "periodSeconds": 30,
                "timeoutSeconds": 5,
                "failureThreshold": 6,
                "successThreshold": 1,
            }
        ]
    return payload


def _upsert_service(
    nf: Northflank,
    project_id: str,
    *,
    hint: str,
    name: str,
    description: str,
    command: str,
    runtime_env: dict[str, str],
    public_http: bool,
) -> str:
    existing = _get_by_hint(nf, f"/projects/{project_id}/services", hint)
    payload = _service_payload(
        name=name,
        description=description,
        command=command,
        runtime_env=runtime_env,
        public_http=public_http,
    )
    if existing:
        service_id = str(existing.get("id", hint))
        nf.request("PATCH", f"/projects/{project_id}/services/combined/{service_id}", json=payload)
        print(f"Updated service: {service_id}")
        return service_id
    data = nf.request("POST", f"/projects/{project_id}/services/combined", json=payload).json().get("data", {})
    service_id = str(data.get("id") or hint)
    print(f"Created service: {service_id}")
    return service_id


def _trigger_build(nf: Northflank, project_id: str, service_id: str) -> None:
    sha = os.getenv("GITHUB_SHA", "").strip()
    payload: dict[str, str] = {}
    if re.fullmatch(r"[0-9a-fA-F]{40}", sha):
        payload["sha"] = sha
    nf.request(
        "POST",
        f"/projects/{project_id}/services/{service_id}/build",
        json=payload,
        allowed=(200, 201, 202, 204),
    )
    print(f"Build triggered: {service_id}")


def _status_words(data: dict[str, Any]) -> tuple[str, str]:
    status = data.get("status", {})
    if isinstance(status, str):
        return "", status.upper()
    if not isinstance(status, dict):
        return "", ""
    build_obj = status.get("build", {})
    deploy_obj = status.get("deployment", {})
    build = str(build_obj.get("status", build_obj) if isinstance(build_obj, dict) else build_obj).upper()
    deploy = str(deploy_obj.get("status", deploy_obj) if isinstance(deploy_obj, dict) else deploy_obj).upper()
    return build, deploy


def _wait_service(nf: Northflank, project_id: str, service_id: str) -> dict[str, Any]:
    deadline = time.time() + 900
    last = ""
    while time.time() < deadline:
        data = nf.get_optional(f"/projects/{project_id}/services/{service_id}")
        if not data:
            raise RuntimeError(f"Service disappeared: {service_id}")
        build, deploy = _status_words(data)
        summary = f"build={build or '?'} deployment={deploy or '?'}"
        if summary != last:
            print(f"{service_id}: {summary}")
            last = summary
        if any(word in build for word in ("FAIL", "CRASH", "ERROR")):
            raise RuntimeError(f"Northflank build failed for {service_id}: {build}")
        if any(word in deploy for word in ("FAIL", "CRASH", "ERROR")):
            raise RuntimeError(f"Northflank deployment failed for {service_id}: {deploy}")
        if build in {"SUCCESS", "SUCCEEDED", "COMPLETED"} and deploy in {"COMPLETED", "SUCCESS", "RUNNING", "HEALTHY"}:
            return data
        time.sleep(10)
    raise RuntimeError(f"Service did not become ready before timeout: {service_id}")


def _public_url(nf: Northflank, project_id: str, service_id: str) -> str:
    response = nf.client.get(f"/projects/{project_id}/services/{service_id}/ports")
    if response.status_code != 200:
        return ""
    payload = response.json().get("data", response.json())

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
        if isinstance(value, list):
            for item in value:
                found = walk(item)
                if found:
                    return found
        return ""

    return walk(payload)


def main() -> int:
    print("Deploying Aircraft Alert v3.2 (Telegram + Gemini Photography) to Northflank...")
    nf = Northflank()
    project_id = _project(nf)
    addon_id = _addon(nf, project_id)
    _wait_for_addon(nf, project_id, addon_id)
    mongo_uri = _mongo_uri(nf, project_id, addon_id)
    print("MongoDB credentials resolved (hidden).")
    env = _runtime_env(mongo_uri)

    web_id = _upsert_service(
        nf,
        project_id,
        hint=WEB_HINT,
        name="Aircraft Web",
        description="Telegram bot, admin API and Gemini photography assistant",
        command="uvicorn app.main:app --host 0.0.0.0 --port 8000",
        runtime_env=env,
        public_http=True,
    )
    worker_id = _upsert_service(
        nf,
        project_id,
        hint=WORKER_HINT,
        name="Aircraft Worker",
        description="Always-on five-second ADS-B monitor and notification worker",
        command="python worker.py",
        runtime_env=env,
        public_http=False,
    )

    for service_id in (web_id, worker_id):
        _trigger_build(nf, project_id, service_id)
    for service_id in (web_id, worker_id):
        _wait_service(nf, project_id, service_id)

    url = _public_url(nf, project_id, web_id)
    print("Northflank web and worker services are running.")
    if url:
        print(f"Public URL: {url}")
        try:
            response = httpx.get(f"{url.rstrip('/')}/health", timeout=20)
            print(f"Health endpoint HTTP {response.status_code}")
            if response.status_code != 200:
                return 1
            data = response.json()
            print(
                "Health summary: bot_mode=%s worker=%s version=%s"
                % (data.get("bot_mode"), (data.get("worker") or {}).get("status"), data.get("version"))
            )
        except Exception as exc:
            print(f"Health endpoint could not be verified: {_redact(str(exc))}")
            return 1
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"DEPLOYMENT ERROR: {_redact(str(exc))}", file=sys.stderr)
        raise SystemExit(1)
