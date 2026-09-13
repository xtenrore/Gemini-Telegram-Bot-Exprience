"""Typed models for the v3.2 photography intelligence subsystem."""
from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, Field


class CameraProfile(BaseModel):
    raw_input: str
    brand: str = ""
    model: str = ""
    camera_type: str = "unknown"
    sensor_format: str = "unknown"
    sensor_megapixels: float | None = None
    has_ibis: bool | None = None
    max_native_iso: int | None = None
    max_burst_fps: float | None = None
    autofocus_summary: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    assumptions: list[str] = Field(default_factory=list)


class LensProfile(BaseModel):
    raw_input: str
    brand: str = ""
    model: str = ""
    min_focal_mm: float | None = None
    max_focal_mm: float | None = None
    max_aperture_wide: float | None = None
    max_aperture_tele: float | None = None
    has_stabilization: bool | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    assumptions: list[str] = Field(default_factory=list)


class SolarContext(BaseModel):
    timestamp: str
    timezone: str
    elevation_deg: float | None = None
    azimuth_deg: float | None = None
    phase: str = "unknown"
    sunrise: str | None = None
    sunset: str | None = None
    dawn: str | None = None
    dusk: str | None = None
    subject_bearing_deg: float | None = None
    sun_subject_angle_deg: float | None = None
    lighting_relationship: str = "unknown"


class WeatherContext(BaseModel):
    timestamp: str = ""
    timezone: str = "UTC"
    temperature_c: float | None = None
    apparent_temperature_c: float | None = None
    relative_humidity_pct: float | None = None
    dew_point_c: float | None = None
    precipitation_mm: float | None = None
    cloud_cover_pct: float | None = None
    visibility_m: float | None = None
    wind_speed_kmh: float | None = None
    wind_direction_deg: float | None = None
    wind_gusts_kmh: float | None = None
    surface_pressure_hpa: float | None = None
    shortwave_radiation_wm2: float | None = None
    direct_normal_irradiance_wm2: float | None = None
    diffuse_radiation_wm2: float | None = None
    soil_temperature_0cm_c: float | None = None
    aerosol_optical_depth: float | None = None
    dust_ugm3: float | None = None
    pm2_5_ugm3: float | None = None
    pm10_ugm3: float | None = None
    uv_index: float | None = None
    heat_haze_signal: int = Field(default=0, ge=0, le=100)
    heat_haze_factors: list[str] = Field(default_factory=list)
    sources_ok: list[str] = Field(default_factory=list)
    source_errors: list[str] = Field(default_factory=list)


class AircraftPhotoContext(BaseModel):
    icao24: str = ""
    aircraft_type: str = ""
    callsign: str = ""
    distance_km: float | None = None
    altitude_m: float | None = None
    speed_ms: float | None = None
    heading_deg: float | None = None
    latitude: float | None = None
    longitude: float | None = None
    eta_seconds: float | None = None
    live: bool = False


class CameraSettings(BaseModel):
    exposure_mode: str = ""
    shutter_speed: str = ""
    aperture: str = ""
    iso: str = ""
    exposure_compensation: str = ""
    autofocus_mode: str = ""
    autofocus_area: str = ""
    drive_mode: str = ""
    stabilization: str = ""
    focal_length: str = ""
    metering: str = ""
    white_balance: str = ""
    file_format: str = ""


class PhotoRecommendation(BaseModel):
    title: str
    quality_score: int = Field(ge=0, le=100)
    confidence: Literal["low", "medium", "high"]
    summary: str
    light_assessment: str
    atmosphere_assessment: str
    heat_haze_assessment: str
    settings: CameraSettings
    technique: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    best_timing: str = ""
    reasoning: list[str] = Field(default_factory=list)
    model_used: str = ""


class PhotographyContext(BaseModel):
    latitude: float
    longitude: float
    camera: CameraProfile
    lens: LensProfile | None = None
    weather: WeatherContext
    solar: SolarContext
    aircraft: AircraftPhotoContext | None = None

    def as_prompt_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)
