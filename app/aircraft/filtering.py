"""Fast deterministic profile/category/aircraft filtering for Plane Alerts v4.3."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import json
from typing import Any

from app.aircraft.operators import operator_from_aircraft
from app.aircraft.registry import categories_for, primary_category


_SYSTEM_RULE: dict[str, Any] = {
    "enabled": True,
    "min_altitude_ft": None,
    "max_altitude_ft": None,
    "airline_mode": "all",
    "airlines": (),
    "radius_km": None,
}


@dataclass(frozen=True, slots=True)
class EffectiveRule:
    enabled: bool
    min_altitude_ft: float | None
    max_altitude_ft: float | None
    airline_mode: str
    airlines: frozenset[str]
    radius_km: float | None


@dataclass(frozen=True, slots=True)
class FilterDecision:
    matched: bool
    radius_km: float
    category: str
    operator_icao: str | None
    reason: str = ""


@dataclass(frozen=True, slots=True)
class CompiledAircraftFilter:
    mode: str
    selected_categories: frozenset[str]
    selected_types: frozenset[str]
    excluded_types: frozenset[str]
    profile_rule: dict[str, Any]
    category_rules: dict[str, dict[str, Any]]
    aircraft_rules: dict[str, dict[str, Any]]

    @property
    def empty_selection(self) -> bool:
        return self.mode != "all" and not self.selected_categories and not self.selected_types

    def type_selected(self, code: str, operator_icao: str | None) -> bool:
        if self.mode == "all":
            return True
        if code in self.selected_types:
            return True
        if code in self.excluded_types:
            return False
        return bool(self.selected_categories.intersection(categories_for(code, operator_icao)))

    def resolved_rule(self, code: str, operator_icao: str | None) -> EffectiveRule:
        merged = dict(_SYSTEM_RULE)
        _apply(merged, self.profile_rule)
        category = primary_category(code, operator_icao)
        _apply(merged, self.category_rules.get(category, {}))
        _apply(merged, self.aircraft_rules.get(code, {}))
        return EffectiveRule(
            enabled=bool(merged.get("enabled", True)),
            min_altitude_ft=_number_or_none(merged.get("min_altitude_ft")),
            max_altitude_ft=_number_or_none(merged.get("max_altitude_ft")),
            airline_mode=str(merged.get("airline_mode") or "all").lower(),
            airlines=frozenset(str(v).upper() for v in (merged.get("airlines") or ())),
            radius_km=_number_or_none(merged.get("radius_km")),
        )

    def evaluate(self, aircraft: Any, base_radius_km: float) -> FilterDecision:
        code = str(getattr(aircraft, "aircraft_type", "") or "UNKNOWN").strip().upper() or "UNKNOWN"
        operator = operator_from_aircraft(aircraft)
        category = primary_category(code, operator)
        radius = float(base_radius_km)
        if not self.type_selected(code, operator):
            return FilterDecision(False, radius, category, operator, "aircraft")

        rule = self.resolved_rule(code, operator)
        if rule.radius_km is not None:
            radius = rule.radius_km
        if not rule.enabled:
            return FilterDecision(False, radius, category, operator, "disabled")

        if rule.min_altitude_ft is not None or rule.max_altitude_ft is not None:
            altitude_m = getattr(aircraft, "altitude", None)
            if altitude_m is None:
                return FilterDecision(False, radius, category, operator, "altitude_missing")
            altitude_ft = float(altitude_m) * 3.280839895
            if rule.min_altitude_ft is not None and altitude_ft < rule.min_altitude_ft:
                return FilterDecision(False, radius, category, operator, "altitude_low")
            if rule.max_altitude_ft is not None and altitude_ft > rule.max_altitude_ft:
                return FilterDecision(False, radius, category, operator, "altitude_high")

        if rule.airline_mode == "whitelist":
            if operator is None or operator not in rule.airlines:
                return FilterDecision(False, radius, category, operator, "airline_whitelist")
        elif rule.airline_mode == "blacklist":
            if operator is not None and operator in rule.airlines:
                return FilterDecision(False, radius, category, operator, "airline_blacklist")

        return FilterDecision(True, radius, category, operator)


def _number_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _apply(target: dict[str, Any], override: dict[str, Any]) -> None:
    # Missing keys mean "Use Parent Setting".  Explicit None at the profile
    # level is meaningful (for example no altitude ceiling), so it is retained.
    for key in _SYSTEM_RULE:
        if key in override:
            target[key] = override[key]


def _legacy_filter(preferences: dict[str, Any]) -> dict[str, Any]:
    from app.aircraft.categories import AIRCRAFT_CATEGORIES

    selected_categories = list(preferences.get("selected_categories") or [])
    disabled = {str(v).upper() for v in (preferences.get("disabled_types") or [])}
    custom = {str(v).upper() for v in (preferences.get("custom_aircraft") or [])}
    if "All Aircraft" in selected_categories:
        return {"mode": "all", "selected_categories": [], "selected_types": [], "excluded_types": []}
    selected_types = set(custom)
    for category in selected_categories:
        selected_types.update(str(v).upper() for v in AIRCRAFT_CATEGORIES.get(category, []) if str(v).upper() not in disabled)
    return {
        "mode": "selected",
        "selected_categories": [],
        "selected_types": sorted(selected_types),
        "excluded_types": sorted(disabled),
    }


def normalized_filter_config(preferences: dict[str, Any]) -> dict[str, Any]:
    raw = preferences.get("aircraft_filter")
    if not isinstance(raw, dict):
        raw = _legacy_filter(preferences)
    mode = "all" if str(raw.get("mode")).lower() == "all" else "selected"
    return {
        "mode": mode,
        "selected_categories": sorted({str(v) for v in (raw.get("selected_categories") or [])}),
        "selected_types": sorted({str(v).upper() for v in (raw.get("selected_types") or [])}),
        "excluded_types": sorted({str(v).upper() for v in (raw.get("excluded_types") or [])}),
    }


def normalized_rule_config(preferences: dict[str, Any]) -> dict[str, Any]:
    raw = preferences.get("filter_rules") if isinstance(preferences.get("filter_rules"), dict) else {}
    return {
        "profile": dict(raw.get("profile") or {}),
        "categories": {str(k): dict(v) for k, v in dict(raw.get("categories") or {}).items() if isinstance(v, dict)},
        "aircraft": {str(k).upper(): dict(v) for k, v in dict(raw.get("aircraft") or {}).items() if isinstance(v, dict)},
    }


def compile_filter(preferences: dict[str, Any]) -> CompiledAircraftFilter:
    """Compile a preference document once per distinct filter fingerprint."""
    payload = {
        "filter": normalized_filter_config(preferences),
        "rules": normalized_rule_config(preferences),
    }
    fingerprint = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return _compile_fingerprint(fingerprint)


@lru_cache(maxsize=512)
def _compile_fingerprint(fingerprint: str) -> CompiledAircraftFilter:
    payload = json.loads(fingerprint)
    selection = payload["filter"]
    rules = payload["rules"]
    return CompiledAircraftFilter(
        mode=selection["mode"],
        selected_categories=frozenset(selection["selected_categories"]),
        selected_types=frozenset(selection["selected_types"]),
        excluded_types=frozenset(selection["excluded_types"]),
        profile_rule=dict(rules["profile"]),
        category_rules={str(k): dict(v) for k, v in rules["categories"].items()},
        aircraft_rules={str(k).upper(): dict(v) for k, v in rules["aircraft"].items()},
    )


def clear_filter_cache() -> None:
    _compile_fingerprint.cache_clear()
