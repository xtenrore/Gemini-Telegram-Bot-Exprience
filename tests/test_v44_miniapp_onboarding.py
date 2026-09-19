from __future__ import annotations

from app.bot.profile_modes_v44 import _visual_url
from app.config import settings
from app.profile_miniapp import PROFILE_SETUP_HTML
from app.profile_miniapp_v44_patch import _onboarding_ready, _patched_html


def test_visual_setup_url_uses_public_origin_not_webhook_path(monkeypatch):
    monkeypatch.setattr(
        settings,
        "webhook_url",
        "https://plane-alerts-production.up.railway.app/webhook",
    )
    assert _visual_url("0123456789") == (
        "https://plane-alerts-production.up.railway.app/"
        "profile-setup-ui/profile/0123456789"
    )
    assert _visual_url("0123456789", onboarding=True) == (
        "https://plane-alerts-production.up.railway.app/"
        "profile-setup-ui/profile/0123456789?onboarding=1"
    )


def test_visual_setup_html_is_ios_safe_and_has_no_visual_setup_brand_kicker():
    html = _patched_html(PROFILE_SETUP_HTML)
    assert "Plane Alerts · Visual Setup" not in html
    assert "<title>Profile Setup</title>" in html
    assert "window.location.origin+path" in html
    assert "complete_setup:onboarding" in html
    assert "new URLSearchParams(window.location.search)" in html


def test_visual_onboarding_requires_location_and_aircraft_selection():
    profile = {
        "config": {
            "location": {"latitude": 41.0, "longitude": 28.8, "radius_km": 15},
            "preferences": {
                "aircraft_filter": {
                    "mode": "selected",
                    "selected_categories": [],
                    "selected_types": [],
                    "excluded_types": [],
                }
            },
        }
    }
    ready, error = _onboarding_ready(profile)
    assert ready is False
    assert "aircraft" in error.lower()

    profile["config"]["preferences"]["aircraft_filter"]["mode"] = "all"
    ready, error = _onboarding_ready(profile)
    assert ready is True
    assert error == ""
