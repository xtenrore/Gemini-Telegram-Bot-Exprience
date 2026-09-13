import pytest

import app.database as database
from app.database import _mongo_target_label


def test_atlas_log_label_hides_credentials():
    uri = "mongodb+srv://plane_user:super-secret@cluster0.example.mongodb.net/?retryWrites=true&w=majority"
    label = _mongo_target_label(uri)
    assert label == "mongodb+srv://cluster0.example.mongodb.net"
    assert "plane_user" not in label
    assert "super-secret" not in label


def test_local_mongo_log_label_is_safe():
    assert _mongo_target_label("mongodb://localhost:27017") == "mongodb://localhost"


@pytest.mark.asyncio
async def test_hot_connect_reuses_existing_db_without_index_scan(monkeypatch):
    existing_db = object()
    monkeypatch.setattr(database, "_client", object())
    monkeypatch.setattr(database, "_db", existing_db)

    calls = 0

    async def fake_indexes(db):
        nonlocal calls
        calls += 1

    monkeypatch.setattr(database, "_ensure_indexes", fake_indexes)

    result = await database.connect_db(ensure_indexes=False)
    assert result is existing_db
    assert calls == 0


@pytest.mark.asyncio
async def test_index_scan_is_cached_for_warm_connection(monkeypatch):
    existing_db = object()
    monkeypatch.setattr(database, "_client", object())
    monkeypatch.setattr(database, "_db", existing_db)
    monkeypatch.setattr(database, "_indexes_ready_for", None)

    calls = 0

    async def fake_indexes(db):
        nonlocal calls
        calls += 1

    monkeypatch.setattr(database, "_ensure_indexes", fake_indexes)

    first = await database.connect_db(ensure_indexes=True)
    second = await database.connect_db(ensure_indexes=True)
    assert first is existing_db
    assert second is existing_db
    assert calls == 1
