from pathlib import Path

from scripts.update_aircraft_registry import refresh


def test_registry_refresh_adds_missing_doc8643_types_without_overwriting_curated_rows(tmp_path: Path):
    registry = tmp_path / "registry.py"
    registry.write_text(
        'header\n_DATA = r"""\nA359|Airbus|A350-900|A350|widebody|A350\n"""\nfooter\n',
        encoding="utf-8",
    )
    csv_text = (
        "Designator,Description,AircraftDescription,EngineCount,EngineType,ManufacturerCode,ModelFullName,WTC\n"
        "A359,L2J,LandPlane,2,Jet,AIRBUS,A350-900,H\n"
        "ZZZZ,L1P,LandPlane,1,Piston,TESTCO,Very Rare Test Aircraft,L\n"
        "H123,H1T,Helicopter,1,Turboprop,ROTORCO,Test Helicopter,L\n"
    )

    added, total = refresh(registry, csv_text)
    text = registry.read_text(encoding="utf-8")

    assert added == 2
    assert total == 3
    assert "A359|Airbus|A350-900|A350|widebody|A350" in text
    assert "ZZZZ|TESTCO|Very Rare Test Aircraft|Very Rare Test Aircraft|other|Very Rare Test Aircraft" in text
    assert "H123|ROTORCO|Test Helicopter|Test Helicopter|helicopter|Test Helicopter" in text
