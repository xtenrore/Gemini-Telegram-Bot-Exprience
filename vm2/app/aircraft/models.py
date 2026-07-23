"""Pydantic models for normalised aircraft data."""

from __future__ import annotations

from pydantic import BaseModel, Field


class NormalizedAircraft(BaseModel):
    """Unified aircraft representation regardless of data source.

    Every provider adapter must convert its raw data into this model so the
    rest of the application can work source-agnostically.
    """

    icao24: str = Field(..., description="ICAO 24-bit address (hex string, lowercase)")
    callsign: str = Field(default="", description="Flight callsign, stripped of whitespace")
    origin_country: str = Field(default="", description="Country of registration")
    latitude: float | None = Field(default=None, description="WGS-84 latitude in degrees")
    longitude: float | None = Field(default=None, description="WGS-84 longitude in degrees")
    altitude: float | None = Field(default=None, description="Barometric altitude in metres")
    velocity: float | None = Field(default=None, description="Ground speed in m/s")
    heading: float | None = Field(default=None, description="True heading in degrees (0-360)")
    aircraft_type: str = Field(default="", description="ICAO type designator (e.g. B738, C17)")
    timestamp: int | None = Field(default=None, description="Unix epoch of last position update")

    @property
    def has_position(self) -> bool:
        """Return True if this aircraft has a valid position fix."""
        return self.latitude is not None and self.longitude is not None

    @property
    def display_type(self) -> str:
        """Human-friendly type string, falling back to 'Unknown'."""
        return self.aircraft_type or "Unknown"
