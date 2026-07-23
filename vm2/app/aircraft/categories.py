"""Preset aircraft categories and ICAO type-code validation."""

from __future__ import annotations

import re

# ── Preset Categories ────────────────────────────────────────────────────────
# Mapping of human-readable category name → set of ICAO type designators.
# Users select one or more categories; matching expands to these codes.

AIRCRAFT_CATEGORIES: dict[str, list[str]] = {
    "Military": [
        "C17",   # Globemaster III
        "KC35",  # KC-135 Stratotanker (ICAO: K35R)
        "K35R",  # KC-135R Stratotanker
        "KC46",  # KC-46 Pegasus (ICAO: K46)
        "K46",   # KC-46 Pegasus
        "KC10",  # KC-10 Extender (ICAO: DC10)
        "P8",    # P-8 Poseidon
        "E3CF",  # E-3 Sentry AWACS
        "E3TF",  # E-3 Sentry AWACS
        "C130",  # Hercules
        "C30J",  # C-130J Super Hercules
        "B52",   # Stratofortress
        "B1",    # B-1 Lancer
        "B1B",   # B-1B Lancer
        "B2",    # B-2 Spirit
        "F16",   # Fighting Falcon
        "F18",   # Hornet
        "FA18",  # Super Hornet
        "F22",   # Raptor
        "F35",   # Lightning II
        "C5M",   # C-5M Super Galaxy
        "C5",    # C-5 Galaxy
        "A10",   # Thunderbolt II (Warthog)
        "E6",    # Mercury
        "RC35",  # RC-135
        "V22",   # Osprey
        "T38",   # Talon
        "E8",    # JSTARS
        "U2",    # Dragon Lady
        "RQ4",   # Global Hawk
        "MQ9",   # Reaper
    ],
    "Large Airliners": [
        "A380",  # Airbus A380
        "A388",  # A380-800
        "B748",  # Boeing 747-8
        "B744",  # Boeing 747-400
        "B77W",  # Boeing 777-300ER
        "B779",  # Boeing 777-9
        "B789",  # Boeing 787-9
        "B78X",  # Boeing 787-10
        "A359",  # Airbus A350-900
        "A35K",  # Airbus A350-1000
        "A346",  # Airbus A340-600
    ],
    "Cargo": [
        "B77L",  # Boeing 777F
        "B77F",  # Boeing 777F alt
        "B748",  # Boeing 747-8F
        "B744",  # Boeing 747-400F
        "MD11",  # MD-11F
        "A332",  # Airbus A330-200F
        "A338",  # Airbus A330-800F (BelugaXL related)
        "AN24",  # Antonov An-124
        "A124",  # Antonov An-124 (ICAO)
        "A225",  # Antonov An-225 Mriya
        "A306",  # Airbus A300-600F (UPS/FedEx)
        "BCS3",  # Beluga (related)
    ],
    "Business Jets": [
        "GLEX",  # Bombardier Global Express
        "GL5T",  # Bombardier Global 5000/5500
        "GL7T",  # Bombardier Global 7500
        "GLF6",  # Gulfstream G650
        "GLF5",  # Gulfstream G550
        "GLF4",  # Gulfstream G450
        "F7X",   # Dassault Falcon 7X
        "F8X",   # Dassault Falcon 8X
        "F900",  # Dassault Falcon 900
        "PC24",  # Pilatus PC-24
        "CL35",  # Bombardier Challenger 350
        "CL60",  # Bombardier Challenger 600 series
        "C68A",  # Cessna Citation Longitude
        "E55P",  # Embraer Praetor 500
        "GA7C",  # Gulfstream G700 (upcoming)
    ],
    "Helicopters": [
        "H60",   # Sikorsky Black Hawk
        "S70",   # Sikorsky S-70 (civilian Black Hawk)
        "CH47",  # Boeing CH-47 Chinook
        "H47",   # Chinook (alt code)
        "AS50",  # Eurocopter AS350 Écureuil
        "EC35",  # Eurocopter EC135
        "EC45",  # Eurocopter EC145
        "B407",  # Bell 407
        "B412",  # Bell 412
        "B429",  # Bell 429
        "AW13",  # AgustaWestland AW139
        "AW18",  # AgustaWestland AW189
        "R44",   # Robinson R44
        "R66",   # Robinson R66
        "S92",   # Sikorsky S-92
    ],
    "Government": [
        "VC25",  # Air Force One (VC-25A)
        "B742",  # VC-25 (Boeing 747-200 variant)
        "C32",   # Air Force Two (Boeing 757)
        "C40",   # C-40 Clipper (Boeing 737)
        "GLF5",  # Government executive jets
        "C37A",  # Gulfstream C-37A
        "C20",   # Gulfstream C-20
        "C12",   # Beechcraft C-12 Huron
    ],
    "Experimental": [
        "WB57",  # NASA WB-57
        "ER2",   # NASA ER-2
        "B52",   # (also used in test programs)
        "U2",    # Dragon Lady (also experimental)
        "SR22",  # Cirrus SR22 (common experimental platform)
    ],
    "VIP Aircraft": [
        "A319",  # Airbus ACJ319
        "A320",  # Airbus ACJ320
        "B737",  # BBJ (Boeing Business Jet)
        "B738",  # BBJ variant
        "B39M",  # BBJ MAX variant
        "A318",  # Airbus ACJ318
        "B762",  # VIP 767-200
        "B763",  # VIP 767-300
    ],
}

# ── Category display info ────────────────────────────────────────────────────
CATEGORY_EMOJIS: dict[str, str] = {
    "Military": "🛩️",
    "Large Airliners": "✈️",
    "Cargo": "📦",
    "Business Jets": "💼",
    "Helicopters": "🚁",
    "Government": "🏛️",
    "Experimental": "🔬",
    "VIP Aircraft": "⭐",
}

# Ordered list for consistent UI display
CATEGORY_ORDER: list[str] = [
    "Military",
    "Large Airliners",
    "Cargo",
    "Business Jets",
    "Helicopters",
    "Government",
    "Experimental",
    "VIP Aircraft",
]

# ── Regex for ICAO type code validation ──────────────────────────────────────
_ICAO_CODE_RE = re.compile(r"^[A-Z0-9]{2,4}$")


def validate_icao_code(code: str) -> bool:
    """Return True if *code* looks like a valid ICAO type designator.

    Rules: 2-4 uppercase alphanumeric characters.
    """
    return bool(_ICAO_CODE_RE.match(code.strip().upper()))


def get_all_types_for_categories(categories: list[str]) -> set[str]:
    """Expand a list of category names into a flat set of ICAO type codes."""
    result: set[str] = set()
    for cat in categories:
        result.update(AIRCRAFT_CATEGORIES.get(cat, []))
    return result


def get_category_display(name: str) -> str:
    """Return the emoji + name string for a category."""
    emoji = CATEGORY_EMOJIS.get(name, "✈️")
    return f"{emoji} {name}"


def resolve_match_prefixes(types: set[str]) -> set[str]:
    """Expands exact ICAO codes into family prefixes to catch all variants.
    
    E.g., if a user adds 'B738', this ensures it matches all 737 variants (B731-B739).
    Military types like F16 also match F16A, F16C, F16CM etc.
    """
    prefixes = set()
    for t in types:
        t = t.upper()
        prefixes.add(t)  # Always add the exact code as a prefix
        
        # Boeing families
        if t.startswith("B73"):
            prefixes.update(["B73", "B38M", "B39M", "B3XM"])
        elif t.startswith("B74"):
            prefixes.add("B74")
        elif t.startswith("B77"):
            prefixes.update(["B77", "B77W", "B77L", "B77F", "B779"])
        elif t.startswith("B78"):
            prefixes.update(["B78", "B788", "B789", "B78X"])
            
        # Airbus families
        elif t.startswith("A32") or t.startswith("A31"):
            prefixes.update(["A318", "A319", "A320", "A321", "A20N", "A21N"])
        elif t.startswith("A33"):
            prefixes.update(["A33", "A332", "A333", "A338", "A339"])
        elif t.startswith("A34"):
            prefixes.update(["A34", "A342", "A343", "A345", "A346"])
        elif t.startswith("A35"):
            prefixes.update(["A35", "A359", "A35K"])
        elif t.startswith("A38"):
            prefixes.update(["A38", "A388"])

        # Military fighters & transports — match all sub-variants
        elif t.startswith("F16"):
            prefixes.update(["F16", "F16A", "F16C", "F16CM", "F16D", "F16E"])
        elif t.startswith("F18") or t.startswith("FA18"):
            prefixes.update(["F18", "FA18", "F18E", "F18F", "F18G"])
        elif t.startswith("F35"):
            prefixes.update(["F35", "F35A", "F35B", "F35C"])
        elif t.startswith("F22"):
            prefixes.update(["F22", "F22A"])
        elif t.startswith("C130") or t.startswith("C30J"):
            prefixes.update(["C130", "C30J", "L100", "C30"])
        elif t.startswith("C17"):
            prefixes.update(["C17", "C17A"])
        elif t.startswith("B52"):
            prefixes.update(["B52", "B52H"])
        elif t.startswith("B1") and len(t) <= 3:
            prefixes.update(["B1", "B1B"])
        elif t.startswith("KC"):
            prefixes.update(["KC10", "KC35", "K35R", "KC46", "K46"])
        elif t.startswith("E3"):
            prefixes.update(["E3CF", "E3TF", "E3"])

        # Embraer regional
        elif t.startswith("E1") or t.startswith("E19"):
            prefixes.update(["E170", "E175", "E190", "E195", "E75L", "E75S"])
            
    return prefixes
