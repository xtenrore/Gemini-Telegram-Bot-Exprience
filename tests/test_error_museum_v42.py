import json
from pathlib import Path


def test_v42_error_museum_fixture_preserves_production_failure_evidence():
    path = Path("docs/error_museum/v42-ist-arrival-false-alert-2026-09-18.json")
    payload = json.loads(path.read_text())
    assert payload["classification"] == "decision-level reconstruction"
    assert payload["raw_adsb_track_available"] is False
    encounters = {item["flight"]: item for item in payload["encounters"]}
    assert encounters["THY2GN"]["old_system"]["stored_projected_cpa_km"] < 5.0
    assert "17.26" in encounters["THY2GN"]["old_system"]["2026-09-18T16:53:09Z"]
    assert encounters["THY7ER"]["old_system"]["stored_projected_cpa_km"] < 8.0
    assert payload["v42_expected_behavior"]["stale_data"].startswith("uncertainty")
