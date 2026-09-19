"""Deterministic airline/operator identity helpers for Plane Alerts v4.3."""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any


@dataclass(frozen=True, slots=True)
class OperatorIdentity:
    icao: str
    iata: str
    name: str
    aliases: tuple[str, ...] = ()


# Runtime matching stores canonical ICAO operator identifiers.  The table is
# deliberately local and can be extended without touching filter logic.
_OPERATORS = (
    OperatorIdentity("THY", "TK", "Turkish Airlines", ("Turkish", "THY", "Turkish Cargo")),
    OperatorIdentity("PGT", "PC", "Pegasus Airlines", ("Pegasus",)),
    OperatorIdentity("SXS", "XQ", "SunExpress", ("Sun Express",)),
    OperatorIdentity("AJT", "VF", "AJet", ("AnadoluJet", "Anadolu Jet")),
    OperatorIdentity("AAL", "AA", "American Airlines", ("American",)),
    OperatorIdentity("DAL", "DL", "Delta Air Lines", ("Delta",)),
    OperatorIdentity("UAL", "UA", "United Airlines", ("United",)),
    OperatorIdentity("SWA", "WN", "Southwest Airlines", ("Southwest",)),
    OperatorIdentity("BAW", "BA", "British Airways", ("British",)),
    OperatorIdentity("DLH", "LH", "Lufthansa", ()),
    OperatorIdentity("AFR", "AF", "Air France", ()),
    OperatorIdentity("KLM", "KL", "KLM", ("KLM Royal Dutch Airlines",)),
    OperatorIdentity("EZY", "U2", "easyJet", ("Easy Jet",)),
    OperatorIdentity("RYR", "FR", "Ryanair", ()),
    OperatorIdentity("WZZ", "W6", "Wizz Air", ("Wizz",)),
    OperatorIdentity("UAE", "EK", "Emirates", ()),
    OperatorIdentity("QTR", "QR", "Qatar Airways", ("Qatar",)),
    OperatorIdentity("ETD", "EY", "Etihad Airways", ("Etihad",)),
    OperatorIdentity("SIA", "SQ", "Singapore Airlines", ("Singapore",)),
    OperatorIdentity("CPA", "CX", "Cathay Pacific", ("Cathay",)),
    OperatorIdentity("KAL", "KE", "Korean Air", ("Korean",)),
    OperatorIdentity("JAL", "JL", "Japan Airlines", ("JAL",)),
    OperatorIdentity("ANA", "NH", "All Nippon Airways", ("ANA",)),
    OperatorIdentity("FDX", "FX", "FedEx Express", ("FedEx", "Federal Express")),
    OperatorIdentity("UPS", "5X", "UPS Airlines", ("UPS",)),
    OperatorIdentity("CLX", "CV", "Cargolux", ("Cargolux Airlines",)),
    OperatorIdentity("CKS", "K4", "Kalitta Air", ("Kalitta",)),
    OperatorIdentity("GTI", "5Y", "Atlas Air", ("Atlas",)),
    OperatorIdentity("ABW", "RU", "AirBridgeCargo", ("Air Bridge Cargo",)),
    OperatorIdentity("BOX", "3S", "AeroLogic", ("Aerologic",)),
    OperatorIdentity("NCR", "N8", "National Airlines", ("National Cargo",)),
    OperatorIdentity("QY", "D0", "DHL Air UK", ("DHL", "DHL Aviation")),
    OperatorIdentity("BCS", "QY", "European Air Transport Leipzig", ("EAT Leipzig", "DHL Europe")),
    OperatorIdentity("TAY", "3V", "ASL Airlines Belgium", ("ASL Belgium",)),
)

BY_ICAO = {op.icao: op for op in _OPERATORS}


def _key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


_LOOKUP: dict[str, str] = {}
for _op in _OPERATORS:
    for _value in (_op.icao, _op.iata, _op.name, *_op.aliases):
        if _value:
            _LOOKUP[_key(_value)] = _op.icao


def normalize_operator_input(value: str) -> str | None:
    """Resolve user-entered airline names/codes to a canonical ICAO code."""
    raw = str(value or "").strip()
    if not raw:
        return None
    hit = _LOOKUP.get(_key(raw))
    if hit:
        return hit
    upper = raw.upper()
    if re.fullmatch(r"[A-Z]{3}", upper):
        # Preserve unknown but syntactically valid ICAO operator codes so users
        # are not blocked while the local alias table catches up.
        return upper
    return None


def normalize_operator_list(value: str) -> tuple[list[str], list[str]]:
    """Return (canonical ICAO codes, unresolved tokens) for comma/newline input."""
    parts = [part.strip() for part in re.split(r"[,;\n]+", value) if part.strip()]
    resolved: list[str] = []
    unresolved: list[str] = []
    for part in parts:
        code = normalize_operator_input(part)
        if code:
            if code not in resolved:
                resolved.append(code)
        else:
            unresolved.append(part)
    return resolved, unresolved


def operator_from_aircraft(aircraft: Any) -> str | None:
    """Extract canonical operator identity without external services or AI."""
    for attr in ("operator_icao", "operator"):
        raw = str(getattr(aircraft, attr, "") or "").strip()
        if raw:
            normalized = normalize_operator_input(raw)
            if normalized:
                return normalized
    callsign = str(getattr(aircraft, "callsign", "") or "").strip().upper()
    match = re.match(r"^([A-Z]{3})[A-Z0-9]*$", callsign)
    return match.group(1) if match else None


def operator_display(code: str) -> str:
    op = BY_ICAO.get(str(code or "").upper())
    return f"{op.name} ({op.icao})" if op else str(code or "").upper()
