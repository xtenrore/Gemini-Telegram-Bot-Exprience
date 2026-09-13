from app.photography.conditions import _heat_haze_signal


def test_heat_haze_signal_increases_with_hot_sunny_calm_surface():
    cool, _ = _heat_haze_signal({
        "temperature_c": 12.0,
        "soil_temperature_0cm_c": 12.0,
        "shortwave_radiation_wm2": 80.0,
        "wind_speed_kmh": 25.0,
        "visibility_m": 30000.0,
    })
    hot, factors = _heat_haze_signal({
        "temperature_c": 34.0,
        "soil_temperature_0cm_c": 47.0,
        "shortwave_radiation_wm2": 850.0,
        "wind_speed_kmh": 3.0,
        "visibility_m": 9000.0,
    })
    assert 0 <= cool <= 100
    assert 0 <= hot <= 100
    assert hot > cool + 40
    assert any("ground" in factor for factor in factors)
    assert any("solar" in factor for factor in factors)


def test_heat_haze_signal_tolerates_missing_values():
    score, factors = _heat_haze_signal({})
    assert score == 0
    assert factors == []
