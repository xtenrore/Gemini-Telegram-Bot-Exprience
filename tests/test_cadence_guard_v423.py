from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from app.worker import cadence_guard_v423 as guard
from app.worker import v35


class _Cursor:
    def __init__(self, documents):
        self._documents = list(documents)
        self._index = 0

    def __aiter__(self):
        self._index = 0
        return self

    async def __anext__(self):
        if self._index >= len(self._documents):
            raise StopAsyncIteration
        item = self._documents[self._index]
        self._index += 1
        return item


class _Collection:
    def __init__(self, documents):
        self.documents = list(documents)
        self.find_calls = 0
        self.find_one_calls = 0

    def find(self, query):
        self.find_calls += 1
        user_id = query.get("user_id")
        allowed = set(query.get("aircraft_icao24", {}).get("$in", []))
        return _Cursor(
            document
            for document in self.documents
            if document.get("user_id") == user_id
            and document.get("aircraft_icao24") in allowed
        )

    async def find_one(self, query, *args, **kwargs):
        self.find_one_calls += 1
        for document in self.documents:
            if all(document.get(key) == value for key, value in query.items()):
                return document
        return None


class _Db:
    def __init__(self, states):
        self.states = states

    def __getitem__(self, name):
        if name == "approach_states":
            return self.states
        raise KeyError(name)


class _Aircraft:
    def __init__(self, icao24: str, age: float = 0.0):
        self.icao24 = icao24
        self.position_age_s = age
        self.data_quality = "live"

    def model_copy(self, *, update):
        copy = _Aircraft(self.icao24, self.position_age_s)
        copy.data_quality = self.data_quality
        for key, value in update.items():
            setattr(copy, key, value)
        return copy


@pytest.mark.asyncio
async def test_match_prefetches_approach_states_once(monkeypatch):
    states = _Collection(
        [
            {"_id": "one", "user_id": 7, "aircraft_icao24": "abc", "active": True},
            {"_id": "two", "user_id": 7, "aircraft_icao24": "def", "active": False},
        ]
    )
    db = _Db(states)

    monkeypatch.setattr(guard, "_ORIGINAL_GET_DB", lambda: db)
    monkeypatch.setattr(guard.monitor, "get_db", guard._cached_get_db)

    async def fake_original_match(user, aircraft_list, results_by_provider):
        collection = guard.monitor.get_db()["approach_states"]
        first = await collection.find_one({"user_id": 7, "aircraft_icao24": "abc"})
        second = await collection.find_one({"user_id": 7, "aircraft_icao24": "def"})
        missing = await collection.find_one({"user_id": 7, "aircraft_icao24": "ghi"})
        assert first["_id"] == "one"
        assert second["_id"] == "two"
        assert missing is None
        return 3

    monkeypatch.setattr(guard, "_ORIGINAL_MATCH", fake_original_match)

    result = await guard._match_user_aircraft_batched(
        {"user_id": 7},
        [_Aircraft("abc"), _Aircraft("def"), _Aircraft("ghi")],
        {},
    )

    assert result == 3
    assert states.find_calls == 1
    assert states.find_one_calls == 0


@pytest.mark.asyncio
async def test_provider_poll_budget_reuses_recent_snapshot(monkeypatch):
    async def slow_poll(self, region, *, cycle_number, stagger_new_regions):
        await asyncio.sleep(0.05)
        raise AssertionError("slow provider poll should be cancelled by the budget")

    monkeypatch.setattr(guard, "_ORIGINAL_POLL", slow_poll)
    monkeypatch.setattr(guard, "_POLL_BUDGET_S", 0.01)

    poller = v35.SharedRegionPoller()
    region = v35.SharedPollRegion(
        key="shared-test",
        users=[],
        latitude=41.0,
        longitude=29.0,
        radius_nm=80,
    )
    poller._snapshots[region.key] = v35.SharedSnapshot(
        fetched_mono=time.monotonic() - 1.0,
        fetched_wall=time.time() - 1.0,
        aircraft=[_Aircraft("abc", 2.0)],
        by_provider={"adsb.fi": [_Aircraft("abc", 2.0)]},
        hot_until_mono=time.monotonic() + 30.0,
        provider_name="adsb.fi",
    )

    result = await guard._poll_bounded(
        poller,
        region,
        cycle_number=1,
        stagger_new_regions=False,
    )

    assert result.cache_hit is True
    assert result.fresh is False
    assert result.provider_name == "budget-continuity"
    assert [aircraft.icao24 for aircraft in result.aircraft] == ["abc"]
    assert result.aircraft[0].position_age_s >= 3.0


@pytest.mark.asyncio
async def test_provider_poll_budget_returns_empty_without_safe_snapshot(monkeypatch):
    async def slow_poll(self, region, *, cycle_number, stagger_new_regions):
        await asyncio.sleep(0.05)
        return v35.PollResult([], {}, True, False, 1, "late", 0.0)

    monkeypatch.setattr(guard, "_ORIGINAL_POLL", slow_poll)
    monkeypatch.setattr(guard, "_POLL_BUDGET_S", 0.01)

    poller = v35.SharedRegionPoller()
    region = v35.SharedPollRegion(
        key="shared-empty",
        users=[],
        latitude=41.0,
        longitude=29.0,
        radius_nm=80,
    )

    result = await guard._poll_bounded(
        poller,
        region,
        cycle_number=1,
        stagger_new_regions=False,
    )

    assert result.aircraft == []
    assert result.cache_hit is True
    assert result.provider_name == "budget-timeout"
