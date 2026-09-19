from copy import deepcopy

import pytest

import app.alert_profiles as profiles


class Cursor:
    def __init__(self, docs):
        self.docs = docs
    def sort(self, key, direction):
        self.docs.sort(key=lambda d: d.get(key))
        return self
    def __aiter__(self):
        self._i = 0
        return self
    async def __anext__(self):
        if self._i >= len(self.docs):
            raise StopAsyncIteration
        item = deepcopy(self.docs[self._i])
        self._i += 1
        return item


def matches(doc, query):
    for key, value in query.items():
        if isinstance(value, dict) and "$in" in value:
            if doc.get(key) not in value["$in"]:
                return False
        elif doc.get(key) != value:
            return False
    return True


class Collection:
    def __init__(self, docs=None):
        self.docs = [deepcopy(d) for d in (docs or [])]
    async def find_one(self, query, projection=None):
        for doc in self.docs:
            if matches(doc, query):
                return deepcopy(doc)
        return None
    def find(self, query, projection=None):
        result = [deepcopy(d) for d in self.docs if matches(d, query)]
        return Cursor(result)
    async def count_documents(self, query, limit=0):
        count = sum(matches(d, query) for d in self.docs)
        return min(count, limit) if limit else count
    async def insert_one(self, doc):
        self.docs.append(deepcopy(doc))
    async def update_one(self, query, update, upsert=False):
        target = next((d for d in self.docs if matches(d, query)), None)
        if target is None and upsert:
            target = deepcopy(query)
            self.docs.append(target)
        if target is None:
            return
        for key, value in update.get("$set", {}).items():
            target[key] = deepcopy(value)
        for key, value in update.get("$setOnInsert", {}).items():
            target.setdefault(key, deepcopy(value))
    async def replace_one(self, query, replacement, upsert=False):
        for i, doc in enumerate(self.docs):
            if matches(doc, query):
                self.docs[i] = deepcopy(replacement)
                return
        if upsert:
            self.docs.append(deepcopy(replacement))
    async def delete_one(self, query):
        for i, doc in enumerate(self.docs):
            if matches(doc, query):
                self.docs.pop(i)
                return


@pytest.fixture
def db(monkeypatch):
    store = {
        "users": Collection(),
        "locations": Collection(),
        "preferences": Collection(),
        "profiles": Collection(),
        "camera_profiles": Collection(),
    }
    monkeypatch.setattr(profiles, "users_col", lambda: store["users"])
    monkeypatch.setattr(profiles, "locations_col", lambda: store["locations"])
    monkeypatch.setattr(profiles, "preferences_col", lambda: store["preferences"])
    monkeypatch.setattr(profiles, "profiles_col", lambda: store["profiles"])
    monkeypatch.setattr(profiles, "camera_profiles_col", lambda: store["camera_profiles"])
    return store


@pytest.mark.asyncio
async def test_existing_settings_migrate_to_default_profile_without_loss(db):
    db["users"].docs.append({"user_id": 1, "setup_complete": True})
    db["locations"].docs.append({"user_id": 1, "latitude": 41.1, "longitude": 28.9, "radius_km": 15})
    db["preferences"].docs.append({"user_id": 1, "selected_categories": ["All Aircraft"], "spotting": {"mode": "Standard aviation"}})

    assert await profiles.migrate_existing_users() == 1
    profile = await profiles.get_active_profile(1)
    assert profile["name"] == "Default"
    assert profile["config"]["location"]["radius_km"] == 15
    assert profile["config"]["preferences"]["spotting"]["mode"] == "Standard aviation"
    assert profile["config"]["preferences"]["aircraft_filter"]["mode"] == "all"

    # Migration is idempotent after restart.
    assert await profiles.migrate_existing_users() == 0
    assert len(db["profiles"].docs) == 1


@pytest.mark.asyncio
async def test_switching_profiles_materializes_independent_configuration(db):
    db["users"].docs.append({"user_id": 1, "setup_complete": True})
    db["locations"].docs.append({"user_id": 1, "latitude": 1.0, "longitude": 2.0, "radius_km": 10})
    db["preferences"].docs.append({"user_id": 1, "selected_categories": ["All Aircraft"]})
    default = await profiles.ensure_default_profile(1)

    config = profiles.blank_config_from(default["config"])
    config["location"] = {"latitude": 3.0, "longitude": 4.0, "radius_km": 25, "geohash": "abc"}
    config["preferences"]["aircraft_filter"] = {
        "mode": "selected", "selected_categories": ["widebody"], "selected_types": [], "excluded_types": []
    }
    second = await profiles.create_profile(1, "Photography", config, activate=True)

    active_loc = await db["locations"].find_one({"user_id": 1})
    active_prefs = await db["preferences"].find_one({"user_id": 1})
    assert active_loc["radius_km"] == 25
    assert active_prefs["aircraft_filter"]["selected_categories"] == ["widebody"]

    await profiles.activate_profile(1, default["profile_id"])
    restored = await db["locations"].find_one({"user_id": 1})
    assert restored["radius_km"] == 10
    assert (await profiles.get_active_profile(1))["profile_id"] == default["profile_id"]
    assert second["profile_id"] != default["profile_id"]


@pytest.mark.asyncio
async def test_duplicate_is_deeply_independent_and_rename_does_not_touch_source(db):
    db["users"].docs.append({"user_id": 1, "setup_complete": True})
    source = await profiles.ensure_default_profile(1)
    duplicate = await profiles.duplicate_profile(1, source["profile_id"])
    duplicate_config = deepcopy(duplicate["config"])
    duplicate_config.setdefault("location", {})["radius_km"] = 33
    await profiles.save_profile(1, duplicate["profile_id"], config=duplicate_config)
    await profiles.rename_profile(1, duplicate["profile_id"], "Everything")

    untouched = await profiles.get_profile(1, source["profile_id"])
    changed = await profiles.get_profile(1, duplicate["profile_id"])
    assert untouched["name"] == "Default"
    assert (untouched["config"].get("location") or {}).get("radius_km") != 33
    assert changed["name"] == "Everything"
    assert changed["config"]["location"]["radius_km"] == 33


@pytest.mark.asyncio
async def test_deleting_active_profile_chooses_valid_fallback_and_never_leaves_zero(db):
    db["users"].docs.append({"user_id": 1, "setup_complete": True})
    first = await profiles.ensure_default_profile(1)
    second = await profiles.create_profile(1, "Second", deepcopy(first["config"]), activate=True)
    fallback = await profiles.delete_profile(1, second["profile_id"])
    assert fallback["profile_id"] == first["profile_id"]
    assert (await profiles.get_active_profile(1))["profile_id"] == first["profile_id"]
    with pytest.raises(ValueError):
        await profiles.delete_profile(1, first["profile_id"])


@pytest.mark.asyncio
async def test_multi_user_profiles_are_isolated_even_with_same_profile_name(db):
    db["users"].docs.extend([
        {"user_id": 1, "setup_complete": True},
        {"user_id": 2, "setup_complete": True},
    ])
    first = await profiles.ensure_default_profile(1)
    second = await profiles.ensure_default_profile(2)
    assert first["profile_id"] != second["profile_id"]

    config = deepcopy(first["config"])
    config.setdefault("location", {})["radius_km"] = 44
    await profiles.save_profile(1, first["profile_id"], config=config)
    other = await profiles.get_profile(2, second["profile_id"])
    assert (other["config"].get("location") or {}).get("radius_km") != 44
    assert await profiles.get_profile(2, first["profile_id"]) is None


@pytest.mark.asyncio
async def test_sync_active_profile_captures_legacy_location_changes(db):
    db["users"].docs.append({"user_id": 1, "setup_complete": True})
    db["locations"].docs.append({"user_id": 1, "latitude": 1.0, "longitude": 2.0, "radius_km": 10})
    db["preferences"].docs.append({"user_id": 1, "selected_categories": ["All Aircraft"]})
    active = await profiles.ensure_default_profile(1)
    await db["locations"].update_one({"user_id": 1}, {"$set": {"radius_km": 21}})
    await profiles.sync_active_profile_from_legacy(1)
    refreshed = await profiles.get_profile(1, active["profile_id"])
    assert refreshed["config"]["location"]["radius_km"] == 21
