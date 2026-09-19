from types import SimpleNamespace

from app.aircraft.filtering import compile_filter
from app.worker.profile_filter_guard_v43 import _maximum_radius


def _aircraft(code, callsign):
    return SimpleNamespace(
        aircraft_type=code,
        callsign=callsign,
        altitude=10000,
        has_position=True,
    )


def test_mixed_passenger_cargo_operator_does_not_make_all_thy_aircraft_cargo():
    prefs = {
        "aircraft_filter": {
            "mode": "selected",
            "selected_categories": ["cargo"],
            "selected_types": [],
            "excluded_types": [],
        },
        "filter_rules": {"profile": {}, "categories": {}, "aircraft": {}},
    }
    compiled = compile_filter(prefs)
    assert not compiled.evaluate(_aircraft("A321", "THY123"), 15).matched
    # Dedicated cargo operators may add Cargo to a shared passenger/freighter
    # airframe when operator metadata provides a safe deterministic hint.
    assert compiled.evaluate(_aircraft("B748", "CLX123"), 15).matched


def test_shared_polling_coverage_expands_to_largest_rule_radius():
    user = {
        "location": {"radius_km": 8},
        "preferences": {
            "aircraft_filter": {
                "mode": "all",
                "selected_categories": [],
                "selected_types": [],
                "excluded_types": [],
            },
            "filter_rules": {
                "profile": {},
                "categories": {"cargo": {"radius_km": 40}},
                "aircraft": {"AN12": {"radius_km": 75}},
            },
        },
    }
    assert _maximum_radius(user) == 75
