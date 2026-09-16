"""Deterministic Sun/Moon position helpers using Astral."""
from __future__ import annotations
from datetime import datetime, timezone
from astral import Observer
from astral.sun import azimuth as sun_azimuth, elevation as sun_elevation
try:
    from astral.moon import azimuth as moon_azimuth, elevation as moon_elevation
except Exception:  # pragma: no cover
    moon_azimuth=moon_elevation=None


def positions(latitude: float, longitude: float, when: datetime | None=None) -> dict[str,float|None]:
    when=when or datetime.now(timezone.utc)
    if when.tzinfo is None: when=when.replace(tzinfo=timezone.utc)
    observer=Observer(latitude=latitude,longitude=longitude)
    result={"sun_azimuth_deg":None,"sun_elevation_deg":None,"moon_azimuth_deg":None,"moon_elevation_deg":None}
    try:
        result["sun_azimuth_deg"]=round(float(sun_azimuth(observer,when)),2); result["sun_elevation_deg"]=round(float(sun_elevation(observer,when)),2)
    except Exception: pass
    if moon_azimuth and moon_elevation:
        try:
            result["moon_azimuth_deg"]=round(float(moon_azimuth(observer,when)),2); result["moon_elevation_deg"]=round(float(moon_elevation(observer,when)),2)
        except Exception: pass
    return result
