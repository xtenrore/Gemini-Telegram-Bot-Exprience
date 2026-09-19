from types import SimpleNamespace
import time

from app.aircraft.filtering import compile_filter
from app.aircraft.operators import normalize_operator_input, normalize_operator_list
from app.aircraft.registry import category_members, search_aircraft


def ac(code="A359", *, altitude_m=3000.0, callsign="THY123"):
    return SimpleNamespace(
        aircraft_type=code,
        altitude=altitude_m,
        callsign=callsign,
        has_position=True,
    )


def prefs(selection=None, rules=None):
    return {
        "aircraft_filter": selection or {
            "mode": "selected",
            "selected_categories": [],
            "selected_types": [],
            "excluded_types": [],
        },
        "filter_rules": rules or {"profile": {}, "categories": {}, "aircraft": {}},
    }


def test_registry_search_maps_aliases_to_canonical_icao_codes():
    assert search_aircraft("A350-900")[0].code == "A359"
    assert any(item.code == "AN12" for item in search_aircraft("Antonov 12"))
    assert any(item.code == "GLF6" for item in search_aircraft("Gulfstream 650"))
    assert any(item.code == "B748" for item in search_aircraft("747"))
    assert any(item.code == "C172" for item in search_aircraft("Skyhawk"))


def test_registry_has_all_required_selector_groups_populated():
    for category in (
        "widebody", "narrowbody", "regional_jet", "turboprop", "cargo",
        "business_jet", "military", "general_aviation", "helicopter", "classic_rare",
    ):
        assert category_members(category), category


def test_all_aircraft_is_logical_and_matches_unknown_future_types():
    compiled = compile_filter(prefs({
        "mode": "all",
        "selected_categories": [],
        "selected_types": [],
        "excluded_types": [],
    }))
    assert compiled.evaluate(ac("ZZZZ"), 15).matched
    assert compiled.evaluate(ac("UNKNOWN"), 15).matched


def test_empty_selection_does_not_match():
    compiled = compile_filter(prefs())
    assert compiled.empty_selection
    assert not compiled.evaluate(ac(), 15).matched


def test_category_selection_and_explicit_exclusion():
    selection = {
        "mode": "selected",
        "selected_categories": ["widebody"],
        "selected_types": [],
        "excluded_types": ["A359"],
    }
    compiled = compile_filter(prefs(selection))
    assert not compiled.evaluate(ac("A359"), 15).matched
    assert compiled.evaluate(ac("A388"), 15).matched
    assert not compiled.evaluate(ac("B738"), 15).matched


def test_aircraft_rule_overrides_category_and_profile_altitude():
    selection = {
        "mode": "selected", "selected_categories": ["cargo"],
        "selected_types": [], "excluded_types": [],
    }
    rules = {
        "profile": {"min_altitude_ft": 0, "max_altitude_ft": 40000},
        "categories": {"cargo": {"min_altitude_ft": 5000, "max_altitude_ft": 30000}},
        "aircraft": {"AN12": {"min_altitude_ft": 8000, "max_altitude_ft": 20000}},
    }
    compiled = compile_filter(prefs(selection, rules))
    assert not compiled.evaluate(ac("AN12", altitude_m=2000), 15).matched  # ~6562 ft
    assert compiled.evaluate(ac("AN12", altitude_m=4000), 15).matched     # ~13123 ft
    assert not compiled.evaluate(ac("AN12", altitude_m=7000), 15).matched  # ~22966 ft


def test_missing_altitude_is_conservative_only_when_an_altitude_rule_exists():
    selection = {"mode": "all", "selected_categories": [], "selected_types": [], "excluded_types": []}
    limited = compile_filter(prefs(selection, {"profile": {"min_altitude_ft": 5000}, "categories": {}, "aircraft": {}}))
    unlimited = compile_filter(prefs(selection))
    assert not limited.evaluate(ac(altitude_m=None), 15).matched
    assert unlimited.evaluate(ac(altitude_m=None), 15).matched


def test_airline_aliases_normalize_without_ai():
    assert normalize_operator_input("Turkish") == "THY"
    assert normalize_operator_input("Turkish Airlines") == "THY"
    assert normalize_operator_input("TK") == "THY"
    assert normalize_operator_input("THY") == "THY"
    assert normalize_operator_input("FedEx") == "FDX"
    resolved, unresolved = normalize_operator_list("Turkish, QR, FedEx")
    assert resolved == ["THY", "QTR", "FDX"]
    assert unresolved == []


def test_airline_whitelist_blacklist_and_unknown_operator():
    selection = {"mode": "all", "selected_categories": [], "selected_types": [], "excluded_types": []}
    whitelist = compile_filter(prefs(selection, {
        "profile": {"airline_mode": "whitelist", "airlines": ["THY"]},
        "categories": {}, "aircraft": {},
    }))
    assert whitelist.evaluate(ac(callsign="THY123"), 15).matched
    assert not whitelist.evaluate(ac(callsign="BAW123"), 15).matched
    assert not whitelist.evaluate(ac(callsign="1234"), 15).matched

    blacklist = compile_filter(prefs(selection, {
        "profile": {"airline_mode": "blacklist", "airlines": ["THY"]},
        "categories": {}, "aircraft": {},
    }))
    assert not blacklist.evaluate(ac(callsign="THY123"), 15).matched
    assert blacklist.evaluate(ac(callsign="BAW123"), 15).matched
    assert blacklist.evaluate(ac(callsign=""), 15).matched


def test_aircraft_specific_operator_rule_wins_and_radius_override_is_returned():
    selection = {"mode": "all", "selected_categories": [], "selected_types": [], "excluded_types": []}
    rules = {
        "profile": {"airline_mode": "all", "radius_km": 20},
        "categories": {"widebody": {"radius_km": 12}},
        "aircraft": {"A359": {"airline_mode": "blacklist", "airlines": ["THY"], "radius_km": 8}},
    }
    compiled = compile_filter(prefs(selection, rules))
    denied = compiled.evaluate(ac("A359", callsign="THY7"), 15)
    allowed = compiled.evaluate(ac("A359", callsign="QTR7"), 15)
    assert not denied.matched
    assert denied.radius_km == 8
    assert allowed.matched and allowed.radius_km == 8
    assert compiled.evaluate(ac("A388", callsign="QTR1"), 15).radius_km == 12


def test_compile_filter_is_cached_and_fast_enough_for_hot_loop():
    config = prefs({"mode": "all", "selected_categories": [], "selected_types": [], "excluded_types": []})
    first = compile_filter(config)
    second = compile_filter(config)
    assert first is second
    aircraft = ac()
    started = time.perf_counter()
    for _ in range(20000):
        assert first.evaluate(aircraft, 15).matched
    assert time.perf_counter() - started < 1.5
