"""Deterministic camera/lens profile resolver.

Known bodies/lenses are resolved without AI. Unknown lenses still get a useful
focal-range profile when the user's text contains a range such as 200-800 mm.
"""
from __future__ import annotations

import re

CAMERAS = {
    "canon r7": dict(brand="Canon", model="EOS R7", camera_type="mirrorless", sensor_format="APS-C", sensor_width_mm=22.3, sensor_height_mm=14.9, crop_factor=1.6, sensor_megapixels=32.5, has_ibis=True, max_native_iso=32000, max_burst_fps=30.0),
    "canon eos r7": dict(brand="Canon", model="EOS R7", camera_type="mirrorless", sensor_format="APS-C", sensor_width_mm=22.3, sensor_height_mm=14.9, crop_factor=1.6, sensor_megapixels=32.5, has_ibis=True, max_native_iso=32000, max_burst_fps=30.0),
    "canon r5": dict(brand="Canon", model="EOS R5", camera_type="mirrorless", sensor_format="full-frame", sensor_width_mm=36.0, sensor_height_mm=24.0, crop_factor=1.0, sensor_megapixels=45.0, has_ibis=True, max_native_iso=51200, max_burst_fps=20.0),
    "canon r6 ii": dict(brand="Canon", model="EOS R6 Mark II", camera_type="mirrorless", sensor_format="full-frame", sensor_width_mm=36.0, sensor_height_mm=24.0, crop_factor=1.0, sensor_megapixels=24.2, has_ibis=True, max_native_iso=102400, max_burst_fps=40.0),
    "nikon z8": dict(brand="Nikon", model="Z8", camera_type="mirrorless", sensor_format="full-frame", sensor_width_mm=35.9, sensor_height_mm=23.9, crop_factor=1.0, sensor_megapixels=45.7, has_ibis=True, max_native_iso=25600, max_burst_fps=20.0),
    "nikon z9": dict(brand="Nikon", model="Z9", camera_type="mirrorless", sensor_format="full-frame", sensor_width_mm=35.9, sensor_height_mm=23.9, crop_factor=1.0, sensor_megapixels=45.7, has_ibis=True, max_native_iso=25600, max_burst_fps=20.0),
    "sony a1": dict(brand="Sony", model="α1", camera_type="mirrorless", sensor_format="full-frame", sensor_width_mm=35.9, sensor_height_mm=24.0, crop_factor=1.0, sensor_megapixels=50.1, has_ibis=True, max_native_iso=32000, max_burst_fps=30.0),
    "sony a7 iv": dict(brand="Sony", model="α7 IV", camera_type="mirrorless", sensor_format="full-frame", sensor_width_mm=35.9, sensor_height_mm=23.9, crop_factor=1.0, sensor_megapixels=33.0, has_ibis=True, max_native_iso=51200, max_burst_fps=10.0),
    "sony a9 iii": dict(brand="Sony", model="α9 III", camera_type="mirrorless", sensor_format="full-frame", sensor_width_mm=35.6, sensor_height_mm=23.8, crop_factor=1.0, sensor_megapixels=24.6, has_ibis=True, max_native_iso=25600, max_burst_fps=120.0),
    "fujifilm x-h2s": dict(brand="Fujifilm", model="X-H2S", camera_type="mirrorless", sensor_format="APS-C", sensor_width_mm=23.5, sensor_height_mm=15.6, crop_factor=1.5, sensor_megapixels=26.1, has_ibis=True, max_native_iso=12800, max_burst_fps=40.0),
}

LENSES = {
    "rf 200-800": dict(brand="Canon", model="RF 200-800mm F6.3-9 IS USM", min_focal_mm=200.0, max_focal_mm=800.0, max_aperture_wide=6.3, max_aperture_tele=9.0, has_stabilization=True),
    "canon rf 200-800": dict(brand="Canon", model="RF 200-800mm F6.3-9 IS USM", min_focal_mm=200.0, max_focal_mm=800.0, max_aperture_wide=6.3, max_aperture_tele=9.0, has_stabilization=True),
    "rf 100-500": dict(brand="Canon", model="RF 100-500mm F4.5-7.1 L IS USM", min_focal_mm=100.0, max_focal_mm=500.0, max_aperture_wide=4.5, max_aperture_tele=7.1, has_stabilization=True),
    "sony 200-600": dict(brand="Sony", model="FE 200-600mm F5.6-6.3 G OSS", min_focal_mm=200.0, max_focal_mm=600.0, max_aperture_wide=5.6, max_aperture_tele=6.3, has_stabilization=True),
    "nikon 180-600": dict(brand="Nikon", model="NIKKOR Z 180-600mm f/5.6-6.3 VR", min_focal_mm=180.0, max_focal_mm=600.0, max_aperture_wide=5.6, max_aperture_tele=6.3, has_stabilization=True),
}


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().replace("mm", "").strip())


def resolve_camera_profile(text: str) -> dict:
    key = _norm(text)
    for name, values in CAMERAS.items():
        if name in key or key in name:
            return {"raw_input": text, **values, "confidence": 0.98, "assumptions": []}
    sensor_format = "unknown"
    if "aps-c" in key or "apsc" in key:
        sensor_format = "APS-C"
    elif "full frame" in key or "full-frame" in key:
        sensor_format = "full-frame"
    return {"raw_input": text, "brand": text.split()[0] if text.split() else "", "model": text.strip(), "camera_type": "unknown", "sensor_format": sensor_format, "confidence": 0.35, "assumptions": ["Exact camera specification is unknown; conservative generic controls will be used."]}


def resolve_lens_profile(text: str) -> dict:
    key = _norm(text)
    for name, values in LENSES.items():
        if _norm(name) in key or key in _norm(name):
            return {"raw_input": text, **values, "confidence": 0.98, "assumptions": []}
    match = re.search(r"(\d{2,4})\s*[-–—]\s*(\d{2,4})", text)
    if match:
        low, high = map(float, match.groups())
        if high < low:
            low, high = high, low
        return {"raw_input": text, "model": text.strip(), "min_focal_mm": low, "max_focal_mm": high, "confidence": 0.75, "assumptions": ["Focal range was parsed from the lens name; aperture/stabilization were not assumed."]}
    prime = re.search(r"(?<![-\d])(\d{2,4})\s*mm", text.lower())
    if prime:
        mm = float(prime.group(1))
        return {"raw_input": text, "model": text.strip(), "min_focal_mm": mm, "max_focal_mm": mm, "confidence": 0.65, "assumptions": ["Focal length was parsed from the lens name."]}
    return {"raw_input": text, "model": text.strip(), "confidence": 0.25, "assumptions": ["Lens range is unknown; framing recommendations will be limited."]}
