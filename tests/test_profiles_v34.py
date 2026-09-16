from app.intelligence.profiles import resolve_camera_profile, resolve_lens_profile


def test_r7_and_rf_200_800_are_deterministic():
    camera = resolve_camera_profile("Canon EOS R7")
    lens = resolve_lens_profile("Canon RF 200-800mm")
    assert camera["sensor_width_mm"] == 22.3
    assert camera["sensor_megapixels"] == 32.5
    assert lens["min_focal_mm"] == 200
    assert lens["max_focal_mm"] == 800


def test_unknown_zoom_still_parses_range_without_ai():
    lens = resolve_lens_profile("Mystery 150-600mm super tele")
    assert (lens["min_focal_mm"], lens["max_focal_mm"]) == (150, 600)
