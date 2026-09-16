from types import SimpleNamespace
from app.intelligence.camera import frame_occupancy, recommend_camera
from app.intelligence.trajectory import HistorySample, predict_trajectory

NOW = 2_000_000_000.0
cam = SimpleNamespace(sensor_format="APS-C", sensor_megapixels=32.5, sensor_width_mm=22.3, sensor_height_mm=14.9, crop_factor=1.6, model="EOS R7", raw_input="Canon R7")
lens = SimpleNamespace(min_focal_mm=200, max_focal_mm=800)


def test_frame_fill_and_clipping():
    x = frame_occupancy(22.3, 14.9, 800, 5, 64.75, 66.8)
    assert x.frame_width_pct > 40
    close = frame_occupancy(22.3, 14.9, 800, 2, 64.75, 66.8)
    assert close.clipping_risk == "High"


def test_recommendation_scales_focal_and_motion():
    hist = [HistorySample(NOW - 10, 41.20, 29, 3500, 420, 180), HistorySample(NOW, 41.18, 29, 3500, 420, 180)]
    p = predict_trajectory(hist, 41, 29, 8, now=NOW)
    r = recommend_camera(camera=cam, lens=lens, aircraft_type="A359", prediction=p, observer_lat=41, observer_lon=29, heat_haze_level="Moderate", light_relationship="side light")
    assert r.shutter_speed.startswith("1/")
    assert 200 <= r.focal_range_mm[0] <= 800
    assert r.best_window_start_s is not None
    assert r.dynamic_focal


def test_prop_blur_mode_intentionally_slows_shutter():
    hist = [HistorySample(NOW - 10, 41.20, 29, 1200, 220, 180), HistorySample(NOW, 41.18, 29, 1200, 220, 180)]
    p = predict_trajectory(hist, 41, 29, 8, now=NOW)
    r = recommend_camera(camera=cam, lens=lens, aircraft_type="", prediction=p, observer_lat=41, observer_lon=29, mode="Prop blur")
    assert r.shutter_speed == "1/320"
