"""Solar geometry and subject-light relationship helpers.

Sun position is computed deterministically with Astral. Gemini receives these
measurements as facts and is responsible for interpreting them photographically.
"""
from __future__ import annotations

import math
from datetime import datetime
from zoneinfo import ZoneInfo

from astral import Observer
from astral.sun import azimuth, elevation, sun

from app.photography.models import SolarContext


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial great-circle bearing from point 1 to point 2 in degrees."""
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def _angle_delta(a: float, b: float) -> float:
    return abs((a - b + 180.0) % 360.0 - 180.0)


def _phase(elev: float) -> str:
    if elev < -18:
        return "astronomical night"
    if elev < -12:
        return "astronomical twilight"
    if elev < -6:
        return "nautical twilight"
    if elev < 0:
        return "blue hour / civil twilight"
    if elev <= 6:
        return "golden hour"
    if elev <= 15:
        return "low sun"
    return "daylight"


def _light_relationship(sun_azimuth: float, subject_bearing: float | None) -> tuple[float | None, str]:
    if subject_bearing is None:
        return None, "unknown"
    delta = _angle_delta(sun_azimuth, subject_bearing)
    if delta <= 35:
        relation = "strong backlight"
    elif delta <= 70:
        relation = "back/side light"
    elif delta <= 110:
        relation = "side light"
    elif delta <= 145:
        relation = "front/side light"
    else:
        relation = "front light"
    return round(delta, 1), relation


def get_solar_context(
    latitude: float,
    longitude: float,
    timezone_name: str,
    *,
    now: datetime | None = None,
    subject_latitude: float | None = None,
    subject_longitude: float | None = None,
) -> SolarContext:
    """Return current sun geometry, twilight phase and subject-light geometry."""
    try:
        tz = ZoneInfo(timezone_name or "UTC")
    except Exception:
        tz = ZoneInfo("UTC")
        timezone_name = "UTC"

    if now is None:
        now = datetime.now(tz)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=tz)
    else:
        now = now.astimezone(tz)

    observer = Observer(latitude=latitude, longitude=longitude)
    try:
        elev = float(elevation(observer, now))
        azi = float(azimuth(observer, now))
    except Exception:
        elev = None
        azi = None

    subject_bearing = None
    if subject_latitude is not None and subject_longitude is not None:
        subject_bearing = bearing_deg(latitude, longitude, subject_latitude, subject_longitude)

    delta = None
    relationship = "unknown"
    if azi is not None:
        delta, relationship = _light_relationship(azi, subject_bearing)

    times: dict[str, datetime] = {}
    try:
        times = sun(observer, date=now.date(), tzinfo=tz)
    except Exception:
        times = {}

    return SolarContext(
        timestamp=now.isoformat(),
        timezone=timezone_name,
        elevation_deg=round(elev, 2) if elev is not None else None,
        azimuth_deg=round(azi, 2) if azi is not None else None,
        phase=_phase(elev) if elev is not None else "unknown",
        sunrise=times.get("sunrise").isoformat() if times.get("sunrise") else None,
        sunset=times.get("sunset").isoformat() if times.get("sunset") else None,
        dawn=times.get("dawn").isoformat() if times.get("dawn") else None,
        dusk=times.get("dusk").isoformat() if times.get("dusk") else None,
        subject_bearing_deg=round(subject_bearing, 1) if subject_bearing is not None else None,
        sun_subject_angle_deg=delta,
        lighting_relationship=relationship,
    )
