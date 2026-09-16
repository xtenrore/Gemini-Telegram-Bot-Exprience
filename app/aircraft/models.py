"""Pydantic models for normalised aircraft data."""
from __future__ import annotations
from pydantic import BaseModel, Field

class NormalizedAircraft(BaseModel):
    """Unified aircraft representation regardless of data source."""
    icao24: str = Field(..., description="ICAO 24-bit address")
    callsign: str = ""
    origin_country: str = ""
    latitude: float | None = None
    longitude: float | None = None
    altitude: float | None = Field(default=None, description="Barometric altitude in metres")
    velocity: float | None = Field(default=None, description="Ground speed in m/s")
    heading: float | None = Field(default=None, description="True track/heading degrees")
    turn_rate: float = 0.0
    vertical_rate_mps: float | None = None
    position_age_s: float | None = None
    data_quality: str = "unknown"
    aircraft_type: str = ""
    timestamp: int | float | None = None

    @property
    def has_position(self)->bool:return self.latitude is not None and self.longitude is not None
    @property
    def display_type(self)->str:return self.aircraft_type or "Unknown"
    @property
    def ground_speed(self)->float|None:return None if self.velocity is None else self.velocity*1.9438444924406
    @property
    def speed(self)->float|None:return self.ground_speed
    @property
    def track(self)->float|None:return self.heading
