"""Tests for OpenSky key manager and rotation."""

import json

from app.aircraft.api_keys import OpenSkyKey, OpenSkyKeyManager
from app.config import settings


def test_opensky_key_manager_loads_environment_credentials(monkeypatch, tmp_path):
    """Hosted deployments should load secrets without credential files."""
    payload = [
        {"clientId": "id1", "clientSecret": "sec1"},
        {"clientId": "id2", "clientSecret": "sec2"},
    ]
    monkeypatch.setattr(settings, "opensky_credentials_json", json.dumps(payload))
    monkeypatch.setattr(settings, "api_keys_dir", str(tmp_path / "missing"))

    km = OpenSkyKeyManager()
    count = km.load_keys()

    assert count == 2
    status = km.get_status()
    assert status.total_keys == 2
    assert status.all_exhausted is False
    assert status.keys[0]["source_file"] == "env:1"


def test_opensky_rotation():
    """Verify rotation behavior when rate limited."""
    km = OpenSkyKeyManager()
    km._keys = [
        OpenSkyKey(client_id="id1", client_secret="sec1", source_file="k1.json"),
        OpenSkyKey(client_id="id2", client_secret="sec2", source_file="k2.json"),
    ]
    km._current_index = 0

    assert km._keys[0].client_id == "id1"
    km.mark_rate_limited()
    assert km._current_index == 1
    assert km._keys[0].rate_limit_hits == 1
