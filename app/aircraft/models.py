"""Pydantic models for normalised aircraft data."""

from __future__ import annotations

from pydantic import BaseModel, Field


class NormalizedAircraft(BaseModel):
    """Unified aircraft representation regardless of data source."""

    icao24: str = Field(..., description="ICAO 24-bit address (hex string, lowercase)")
    callsign: str = Field(default="", description="Flight callsign, stripped of whitespace")
    origin_country: str = Field(default="", description="Country of registration")
    latitude: float | None = Field(default=None, description="WGS-84 latitude in degrees")
    longitude: float | None = Field(default=None, description="WGS-84 longitude in degrees")
    altitude: float | None = Field(default=None, description="Barometric altitude in metres")
    velocity: float | None = Field(default=None, description="Ground speed in m/s")
    heading: float | None = Field(default=None, description="True track/heading in degrees (0-360)")
    turn_rate: float = Field(default=0.0, description="Track turn rate in degrees/second")
    aircraft_type: str = Field(default="", description="ICAO type designator (e.g. B738, C17)")
    timestamp: int | float | None = Field(default=None, description="Provider timestamp / age field")

    @property
    def has_position(self) -> bool:
        return self.latitude is not None and self.longitude is not None

    @property
    def display_type(self) -> str:
        return self.aircraft_type or "Unknown"

    @property
    def ground_speed(self) -> float | None:
        """Ground speed in knots for the kinematics engine.

        Providers normalize speed to metres/second, while trajectory prediction
        consumes knots. This compatibility property prevents silent zero-speed
        forecasts in the monitor loop.
        """
        if self.velocity is None:
            return None
        return self.velocity * 1.9438444924406

    @property
    def speed(self) -> float | None:
        """Backward-compatible alias for ground speed in knots."""
        return self.ground_speed

    @property
    def track(self) -> float | None:
        """Backward-compatible alias used by the monitoring loop."""
        return self.heading
