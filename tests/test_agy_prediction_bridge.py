from __future__ import annotations

import json
import logging
from pathlib import Path

from app import agy_prediction_bridge as bridge


def test_sanitize_removes_secrets_coordinates_and_pseudonymizes_user():
    result = bridge._sanitize(
        {
            "_id": "abc",
            "user_id": 12345,
            "observer_latitude": 41.0,
            "observer_longitude": 29.0,
            "latitude": 40.9,
            "longitude": 28.9,
            "api_key": "never-show-this",
            "token": "never-show-this-either",
            "callsign": "THY1017",
            "time_to_cpa_s": 220.0,
            "points": [{"lat": 1, "lon": 2}, {"lat": 3, "lon": 4}],
        }
    )
    assert result["record_id"] == "abc"
    assert result["callsign"] == "THY1017"
    assert result["time_to_cpa_s"] == 220.0
    assert result["point_count"] == 2
    assert result["user_ref"].startswith("u-")
    serialized = json.dumps(result)
    assert "12345" not in serialized
    assert "41.0" not in serialized
    assert "never-show" not in serialized


def test_finding_is_handed_off_immediately_without_mongo(tmp_path: Path, monkeypatch, caplog):
    findings = tmp_path / "findings.jsonl"
    cursor = tmp_path / "cursor.json"
    findings.write_text(
        json.dumps(
            {
                "seq": 7,
                "finding_id": "agy-test-7",
                "severity": "high",
                "category": "eta_accuracy",
                "summary": "ETA was unstable",
                "evidence": "prediction shifted repeatedly before CPA",
                "status": "new",
            }
        ) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(bridge, "FINDINGS_FILE", findings)
    monkeypatch.setattr(bridge, "HANDOFF_CURSOR_FILE", cursor)
    monkeypatch.setattr(bridge, "_db", lambda: None)

    with caplog.at_level(logging.WARNING):
        published = bridge.sync_findings_to_handoff()

    assert published == 1
    assert json.loads(cursor.read_text())["seq"] == 7
    assert "CHATGPT_HANDOFF_JSON" in caplog.text
    assert "ETA was unstable" in caplog.text
    # Cursor prevents duplicate ChatGPT deliveries on the next 3-second poll.
    assert bridge.sync_findings_to_handoff() == 0
