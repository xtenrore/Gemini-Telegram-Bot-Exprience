"""Tests for aircraft categories and validation."""

from app.aircraft.categories import (
    AIRCRAFT_CATEGORIES,
    CATEGORY_ORDER,
    get_all_types_for_categories,
    resolve_match_prefixes,
    validate_icao_code,
)


def test_category_definitions():
    """Verify all defined categories have type codes."""
    assert len(CATEGORY_ORDER) > 0
    for cat in CATEGORY_ORDER:
        assert cat in AIRCRAFT_CATEGORIES
        types = AIRCRAFT_CATEGORIES[cat]
        assert len(types) > 0
        for code in types:
            assert isinstance(code, str)
            assert len(code) > 0


def test_get_all_types_for_categories():
    """Verify category expansion."""
    types = get_all_types_for_categories(["Helicopters"])
    assert "H60" in types
    assert "CH47" in types


def test_resolve_match_prefixes():
    """Verify aircraft family prefix matching."""
    prefixes = resolve_match_prefixes({"B738"})
    assert "B73" in prefixes
    assert "B38M" in prefixes

    military_prefixes = resolve_match_prefixes({"F16"})
    assert "F16" in military_prefixes
    assert "F16C" in military_prefixes


def test_validate_icao_code():
    """Verify ICAO code validator."""
    assert validate_icao_code("B738") is True
    assert validate_icao_code("A320") is True
    assert validate_icao_code("C17") is True
    assert validate_icao_code("B1") is True
    assert validate_icao_code("H60") is True

    # Invalid codes
    assert validate_icao_code("") is False
    assert validate_icao_code("TOOLONGCODE") is False
    assert validate_icao_code("A") is False
    assert validate_icao_code("B 73") is False
    assert validate_icao_code("!@#$") is False
