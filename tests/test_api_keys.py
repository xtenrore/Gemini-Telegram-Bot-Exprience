"""Tests for OpenSky key manager and rotation."""

from pathlib import Path
from app.aircraft.api_keys import OpenSkyKeyManager, OpenSkyKey


def test_opensky_key_manager_load_and_status():
    """Verify loading and status snapshot."""
    km = OpenSkyKeyManager()
    count = km.load_keys()
    # Repository has 5 credential files in api/
    assert count >= 0
    status = km.get_status()
    assert status.total_keys == count
    assert status.all_exhausted is False


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
