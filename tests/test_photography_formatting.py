from app.photography.formatting import camera_profile_message, recommendation_message
from app.photography.models import (
    CameraProfile,
    CameraSettings,
    PhotoRecommendation,
    PhotographyContext,
    SolarContext,
    WeatherContext,
)


def test_camera_profile_escapes_untrusted_text():
    camera = CameraProfile(raw_input="x", brand="A&B", model="<R7>", confidence=0.9)
    text = camera_profile_message(camera)
    assert "A&amp;B" in text
    assert "&lt;R7&gt;" in text


def test_recommendation_is_telegram_safe_and_contains_settings():
    rec = PhotoRecommendation(
        title="Fast jet <pass>",
        quality_score=82,
        confidence="high",
        summary="good",
        light_assessment="side light",
        atmosphere_assessment="clear",
        heat_haze_assessment="low",
        settings=CameraSettings(
            exposure_mode="M + Auto ISO",
            shutter_speed="1/2000 s",
            aperture="f/7.1",
            iso="Auto ISO 100-3200",
            exposure_compensation="0 EV",
            autofocus_mode="AF-C",
            autofocus_area="tracking",
            drive_mode="high burst",
            stabilization="on",
            focal_length="400 mm",
            metering="matrix",
            white_balance="auto",
            file_format="RAW",
        ),
        best_timing="now",
        model_used="gemini-3.8-flash",
    )
    context = PhotographyContext(
        latitude=41.0,
        longitude=29.0,
        camera=CameraProfile(raw_input="Canon R7", model="R7", confidence=1),
        weather=WeatherContext(),
        solar=SolarContext(timestamp="x", timezone="UTC"),
    )
    text = recommendation_message(rec, context)
    assert "1/2000 s" in text
    assert "Fast jet &lt;pass&gt;" in text
    assert len(text) < 4096
