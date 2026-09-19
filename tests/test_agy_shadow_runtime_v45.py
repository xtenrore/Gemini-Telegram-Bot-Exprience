from pathlib import Path


class _FakeCollection:
    def __init__(self):
        self.indexes = []

    def create_index(self, spec, **kwargs):
        self.indexes.append((spec, kwargs))
        return "idx"


class _FakeDatabase:
    def __init__(self):
        self.collections = {}

    def __getitem__(self, name):
        return self.collections.setdefault(name, _FakeCollection())


def test_prediction_lab_shadow_queries_have_dedicated_indexes():
    source = Path("app/database.py").read_text()
    assert 'db["flight_route_samples"].create_index("utc_date")' in source
    assert 'db["prediction_sentinel_routes"].create_index("utc_date")' in source
    assert '[("kind", 1), ("status", 1), ("utc_date", 1)]' in source
    assert '[("kind", 1), ("status", 1), ("window_end", 1)]' in source


def test_bridge_creates_shadow_indexes_with_sync_client(monkeypatch):
    from scripts import agy_bridge_daemon as bridge

    database = _FakeDatabase()
    monkeypatch.setattr(bridge.next_hour_base, "_db", lambda: database)
    bridge._ensure_shadow_indexes()

    flight_indexes = database["flight_route_samples"].indexes
    sentinel_indexes = database["prediction_sentinel_routes"].indexes
    audit_indexes = database["prediction_lab_audit"].indexes

    assert ("utc_date", {}) in flight_indexes
    assert ("utc_date", {}) in sentinel_indexes
    assert ([('kind', 1), ('status', 1), ('utc_date', 1)], {}) in audit_indexes
    assert ([('kind', 1), ('status', 1), ('window_end', 1)], {}) in audit_indexes


def test_shadow_failures_back_off_at_normal_cadence_and_stagger_startup():
    source = Path("scripts/agy_bridge_daemon.py").read_text()
    assert "SENTINEL_INITIAL_DELAY_S = 30.0" in source
    assert "next_sentinel_audit = started + SENTINEL_INITIAL_DELAY_S" in source
    assert "next_hour_audit = now + NEXT_HOUR_INTERVAL_S" in source
    assert "next_sentinel_audit = now + SENTINEL_INTERVAL_S" in source
    assert "next_hour_audit = now + 15.0" not in source
    assert "next_sentinel_audit = now + 30.0" not in source


def test_agy_goal_tracks_minimal_python_image_without_global_bypass():
    entrypoint = Path("scripts/agy-worker-entrypoint.sh").read_text()
    assert "Python standard library only" in entrypoint
    assert "numpy, pandas, scipy" in entrypoint
    assert "do not pip-install packages at runtime" in entrypoint
    assert "marker = '\\n\\n[HEADLESS_TOOLING_RULES]'" in entrypoint
    assert "'command(jq)'," not in entrypoint
    assert "--dangerously-skip-permissions" not in entrypoint
