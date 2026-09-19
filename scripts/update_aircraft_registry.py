#!/usr/bin/env python3
"""Refresh Plane Alerts' local aircraft registry from the OpenSky Doc 8643 snapshot.

This is a maintenance/build-time tool. The live alert loop never downloads type
metadata. Existing hand-curated entries keep their richer aliases/categories;
missing designators are appended so the selector/search can cover the complete
snapshot without rewriting Telegram menu code.
"""
from __future__ import annotations

import argparse
import csv
import io
from pathlib import Path
import re
from urllib.request import Request, urlopen

DEFAULT_SOURCE = "https://s3.opensky-network.org/data-samples/metadata/doc8643AircraftTypes.csv"
ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "app" / "aircraft" / "registry.py"

DATA_RE = re.compile(r'(_DATA\s*=\s*r?"""\n)(.*?)(\n"""\n)', re.DOTALL)
CODE_RE = re.compile(r"^[A-Z0-9]{2,4}$")


def _clean(value: object) -> str:
    return " ".join(str(value or "").replace("|", "/").split()).strip()


def _existing_rows(source: str) -> dict[str, str]:
    match = DATA_RE.search(source)
    if not match:
        raise RuntimeError("Could not locate _DATA block in app/aircraft/registry.py")
    rows: dict[str, str] = {}
    for raw in match.group(2).splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        code = line.split("|", 1)[0].strip().upper()
        if CODE_RE.fullmatch(code):
            rows[code] = line
    return rows


def _download(url: str) -> str:
    request = Request(url, headers={"User-Agent": "Plane-Alerts-registry-refresh/4.3"})
    with urlopen(request, timeout=30) as response:  # noqa: S310 - fixed/explicit maintenance URL
        return response.read().decode("utf-8-sig")


def _dedupe_source(text: str) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for row in csv.DictReader(io.StringIO(text)):
        code = _clean(row.get("Designator")).upper()
        if not CODE_RE.fullmatch(code):
            continue
        candidate = {
            "code": code,
            "description": _clean(row.get("Description")),
            "aircraft_description": _clean(row.get("AircraftDescription")),
            "engine_count": _clean(row.get("EngineCount")),
            "engine_type": _clean(row.get("EngineType")),
            "manufacturer": _clean(row.get("ManufacturerCode")) or "Unknown",
            "model": _clean(row.get("ModelFullName")) or code,
        }
        previous = result.get(code)
        if previous is None or len(candidate["model"]) > len(previous["model"]):
            result[code] = candidate
    return result


def _category(row: dict[str, str]) -> str:
    description = row["description"].casefold()
    aircraft_description = row["aircraft_description"].casefold()
    engine_type = row["engine_type"].casefold()
    model = row["model"].casefold()

    if "helicopter" in aircraft_description or description.startswith("h"):
        return "helicopter"
    if any(word in model for word in ("glider", "sailplane")):
        return "general_aviation"
    if "turboprop" in engine_type:
        return "turboprop"
    if any(word in model for word in ("gulfstream", "citation", "challenger", "falcon", "global ")):
        return "business_jet"
    # Doc 8643 intentionally does not encode every Plane Alerts role/body-size
    # distinction. Unknown additions remain selectable/searchable under Other;
    # richer categories can be curated later without changing UI code.
    return "other"


def _new_row(row: dict[str, str]) -> str:
    code = row["code"]
    manufacturer = row["manufacturer"]
    model = row["model"]
    family = model
    category = _category(row)
    aliases = model
    return "|".join((code, manufacturer, model, family, category, aliases))


def refresh(registry_path: Path, source_text: str) -> tuple[int, int]:
    source = registry_path.read_text(encoding="utf-8")
    existing = _existing_rows(source)
    remote = _dedupe_source(source_text)

    added = 0
    for code, row in remote.items():
        if code not in existing:
            existing[code] = _new_row(row)
            added += 1

    lines = [existing[code] for code in sorted(existing)]
    replacement = "\n".join(lines)
    updated, count = DATA_RE.subn(lambda m: m.group(1) + replacement + m.group(3), source, count=1)
    if count != 1:
        raise RuntimeError("Registry data block replacement failed")
    registry_path.write_text(updated, encoding="utf-8")
    return added, len(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=DEFAULT_SOURCE, help="Doc8643 CSV URL")
    parser.add_argument("--file", type=Path, help="Use a local Doc8643 CSV instead of downloading")
    parser.add_argument("--registry", type=Path, default=REGISTRY_PATH, help="Registry file to update")
    args = parser.parse_args()

    text = args.file.read_text(encoding="utf-8-sig") if args.file else _download(args.source)
    added, total = refresh(args.registry, text)
    print(f"Aircraft registry refreshed: {added} new designators, {total} total local entries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
