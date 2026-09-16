from app.intelligence.environment import (
    FlightLevelAtmosphere,
    PressureLayer,
    aircraft_az_el,
    altitude_to_pressure_hpa,
    detect_crossing,
    estimate_atmosphere,
    estimate_contrail,
    interpolate_flight_level,
)
from app.intelligence.trajectory import ProjectedPoint


def test_pressure_conversion_reasonable_at_11km():
    assert 210 < altitude_to_pressure_hpa(11000) < 240


def test_pressure_interpolation_and_rhi():
    layers = [PressureLayer(300, 9000, -42, 55, 60, 270), PressureLayer(200, 12000, -56, 80, 80, 280)]
    a = interpolate_flight_level(layers, 10500)
    assert -56 < a.temperature_c < -42
    assert a.ice_relative_humidity_pct > a.relative_humidity_pct
    assert a.confidence in {"High", "Medium"}


def test_contrail_persistent_cold_moist():
    a = FlightLevelAtmosphere(240, -52, 80, 112, 80, 270, "High")
    c = estimate_contrail(a, "A359")
    assert c.formation in {"Likely", "Very Likely"}
    assert c.persistence in {"Persistent", "Persistent/spreading likely"}


def test_contrail_dry_reduces_persistence():
    a = FlightLevelAtmosphere(240, -52, 25, 40, 80, 270, "High")
    c = estimate_contrail(a)
    assert c.persistence in {"Immediate dissipation likely", "Short-lived"}


def test_heat_haze_and_clarity():
    x = estimate_atmosphere(temperature_c=31, surface_temperature_c=47, humidity_pct=55, visibility_m=18000, wind_kmh=4, solar_wm2=850, sun_elevation_deg=50, distance_km=12, pm25=12, aerosol_optical_depth=.2, precipitation_mm=0)
    assert x.heat_haze in {"High", "Severe", "Moderate"}
    assert 0 <= x.clarity_score <= 100


def test_crossing_detection_and_solar_warning():
    p = [ProjectedPoint(20, 41.0, 29.01, 1, 4, 4000, 90)]
    az, el = aircraft_az_el(41, 29, p[0])
    x = detect_crossing(p, 41, 29, az, el, threshold_deg=1, solar=True)
    assert x.candidate and x.min_separation_deg == 0
    assert "never look" in x.safety_warning.lower()
