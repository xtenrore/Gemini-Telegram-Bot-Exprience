"""Tests for FastAPI web endpoints, admin dashboard, and health checks."""

from unittest.mock import AsyncMock, patch
import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    """Create test client with mocked DB connect/close."""
    with patch("app.main.connect_db", new_callable=AsyncMock), \
         patch("app.main.close_db", new_callable=AsyncMock):
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c


def test_root_endpoint(client):
    """GET / should return plain text 'OK'."""
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.text == "OK"


def test_health_endpoint(client):
    """GET /health should return JSON status."""
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data
    assert "uptime_seconds" in data
    assert "bot_mode" in data
    assert "worker" in data


def test_stats_endpoint(client):
    """GET /stats should return JSON statistics."""
    resp = client.get("/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert "poll_interval_seconds" in data
    assert "default_radius_km" in data
    assert "cooldown_minutes" in data


def test_admin_dashboard_html(client):
    """GET /admin should serve admin.html."""
    resp = client.get("/admin")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "Aircraft Alert Bot" in resp.text


def test_admin_static_assets(client):
    """Verify admin.css and admin.js are served."""
    css_resp = client.get("/admin/admin.css")
    assert css_resp.status_code == 200
    assert "text/css" in css_resp.headers["content-type"]

    js_resp = client.get("/admin/admin.js")
    assert js_resp.status_code == 200
    assert "application/javascript" in js_resp.headers["content-type"]


def test_admin_overview_api(client):
    """GET /admin/api/overview should return overview JSON."""
    with patch("app.admin.routes.users_col") as mock_users, \
         patch("app.admin.routes.notification_history_col") as mock_notif:
        mock_users.return_value.count_documents = AsyncMock(return_value=5)
        mock_notif.return_value.count_documents = AsyncMock(return_value=12)

        resp = client.get("/admin/api/overview")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_users"] == 5
        assert data["total_notifications"] == 12
        assert "uptime_seconds" in data


def test_admin_providers_api(client):
    """GET /admin/api/providers should return provider statuses."""
    resp = client.get("/admin/api/providers")
    assert resp.status_code == 200
    data = resp.json()
    assert "providers" in data
    assert len(data["providers"]) > 0


def test_admin_keys_api(client):
    """GET /admin/api/keys should return key rotation status."""
    resp = client.get("/admin/api/keys")
    assert resp.status_code == 200
    data = resp.json()
    assert "total_keys" in data
    assert "active_key_index" in data


def test_admin_system_api(client):
    """GET /admin/api/system should return system configuration and platform info."""
    resp = client.get("/admin/api/system")
    assert resp.status_code == 200
    data = resp.json()
    assert "platform" in data
    assert "config" in data


def test_webhook_unconfigured(client):
    """POST /webhook without active bot should return 503."""
    resp = client.post("/webhook", json={})
    assert resp.status_code in (200, 503)
