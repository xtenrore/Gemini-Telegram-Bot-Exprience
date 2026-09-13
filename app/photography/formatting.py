"""Telegram-safe formatting for photography results."""
from __future__ import annotations

from html import escape

from app.photography.models import CameraProfile, LensProfile, PhotoRecommendation, PhotographyContext, SolarContext, WeatherContext


def _e(value: object) -> str:
    return escape(str(value), quote=True)


def _fmt(value: float | None, suffix: str = "", digits: int = 0) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}{suffix}"


def camera_profile_message(camera: CameraProfile) -> str:
    name = " ".join(part for part in (camera.brand, camera.model) if part).strip() or camera.raw_input
    lines = ["📷 <b>Camera saved</b>", f"<b>Detected:</b> {_e(name)}", f"<b>Type:</b> {_e(camera.camera_type)}", f"<b>Sensor:</b> {_e(camera.sensor_format)}"]
    if camera.sensor_megapixels is not None:
        lines.append(f"<b>Resolution:</b> {camera.sensor_megapixels:g} MP")
    if camera.has_ibis is not None:
        lines.append(f"<b>IBIS:</b> {'Yes' if camera.has_ibis else 'No'}")
    lines.append(f"<b>Gemini confidence:</b> {camera.confidence * 100:.0f}%")
    if camera.assumptions:
        lines.append("\n<i>Assumptions:</i> " + _e("; ".join(camera.assumptions[:2])))
    lines.append("\nUse /lens to add your lens, or /photo for settings right now.")
    return "\n".join(lines)


def lens_profile_message(lens: LensProfile) -> str:
    name = " ".join(part for part in (lens.brand, lens.model) if part).strip() or lens.raw_input
    lines = ["🔭 <b>Lens saved</b>", f"<b>Detected:</b> {_e(name)}"]
    if lens.min_focal_mm is not None or lens.max_focal_mm is not None:
        focal = f"{lens.min_focal_mm:g}–{lens.max_focal_mm:g} mm" if lens.min_focal_mm is not None and lens.max_focal_mm is not None else f"{(lens.max_focal_mm or lens.min_focal_mm):g} mm"
        lines.append(f"<b>Focal range:</b> {focal}")
    if lens.max_aperture_wide is not None:
        aperture = f"f/{lens.max_aperture_wide:g}"
        if lens.max_aperture_tele is not None and lens.max_aperture_tele != lens.max_aperture_wide:
            aperture += f"–f/{lens.max_aperture_tele:g}"
        lines.append(f"<b>Max aperture:</b> {aperture}")
    if lens.has_stabilization is not None:
        lines.append(f"<b>Lens stabilization:</b> {'Yes' if lens.has_stabilization else 'No'}")
    lines.append(f"<b>Gemini confidence:</b> {lens.confidence * 100:.0f}%")
    if lens.assumptions:
        lines.append("\n<i>Assumptions:</i> " + _e("; ".join(lens.assumptions[:2])))
    lines.append("\nUse /photo to calculate a setup for the current conditions.")
    return "\n".join(lines)


def conditions_message(weather: WeatherContext, solar: SolarContext) -> str:
    lines = [
        "🌤️ <b>Photography conditions now</b>",
        f"☀️ Sun: {_fmt(solar.elevation_deg, '°', 1)} elevation, {_fmt(solar.azimuth_deg, '°', 0)} azimuth — {_e(solar.phase)}",
        f"🌡️ Air: {_fmt(weather.temperature_c, '°C', 1)} · RH {_fmt(weather.relative_humidity_pct, '%', 0)} · dew point {_fmt(weather.dew_point_c, '°C', 1)}",
        f"👁️ Visibility: {_fmt((weather.visibility_m or 0) / 1000 if weather.visibility_m is not None else None, ' km', 1)} · cloud {_fmt(weather.cloud_cover_pct, '%', 0)}",
        f"💨 Wind: {_fmt(weather.wind_speed_kmh, ' km/h', 1)} · gusts {_fmt(weather.wind_gusts_kmh, ' km/h', 1)}",
        f"🔆 Solar: {_fmt(weather.shortwave_radiation_wm2, ' W/m²', 0)} · DNI {_fmt(weather.direct_normal_irradiance_wm2, ' W/m²', 0)}",
        f"🌫️ AOD: {_fmt(weather.aerosol_optical_depth, '', 2)} · PM2.5 {_fmt(weather.pm2_5_ugm3, ' µg/m³', 1)} · dust {_fmt(weather.dust_ugm3, ' µg/m³', 1)}",
        f"♨️ Heat-haze signal: <b>{weather.heat_haze_signal}/100</b>",
    ]
    if weather.heat_haze_factors:
        lines.append("<i>Signal factors:</i> " + _e("; ".join(weather.heat_haze_factors[:4])))
    if weather.source_errors:
        lines.append("⚠️ Some inputs unavailable: " + _e("; ".join(weather.source_errors[:2])))
    lines.append("\nUse /photo for Gemini's camera-specific settings.")
    return "\n".join(lines)


def recommendation_message(rec: PhotoRecommendation, context: PhotographyContext) -> str:
    s = rec.settings
    aircraft = context.aircraft
    subject = "next matching aircraft"
    if aircraft:
        label = aircraft.callsign or aircraft.aircraft_type or aircraft.icao24 or "aircraft"
        subject = label + (" (live)" if aircraft.live else " (recent alert)")
    lines = [
        f"📸 <b>{_e(rec.title)}</b>", f"<b>Subject:</b> {_e(subject)}", f"<b>Opportunity:</b> {rec.quality_score}/100 · confidence {_e(rec.confidence)}", "",
        "<b>Use these settings</b>", f"• Mode: <b>{_e(s.exposure_mode)}</b>", f"• Shutter: <b>{_e(s.shutter_speed)}</b>", f"• Aperture: <b>{_e(s.aperture)}</b>", f"• ISO: <b>{_e(s.iso)}</b>",
        f"• Exposure comp: {_e(s.exposure_compensation)}", f"• AF: {_e(s.autofocus_mode)} · {_e(s.autofocus_area)}", f"• Drive: {_e(s.drive_mode)}", f"• Focal length: {_e(s.focal_length)}",
        f"• Stabilization: {_e(s.stabilization)}", f"• Metering: {_e(s.metering)}", f"• WB: {_e(s.white_balance)} · {_e(s.file_format)}", "",
        f"💡 <b>Light:</b> {_e(rec.light_assessment)}", f"🌫️ <b>Atmosphere:</b> {_e(rec.atmosphere_assessment)}", f"♨️ <b>Heat haze:</b> {_e(rec.heat_haze_assessment)}", f"⏱️ <b>Timing:</b> {_e(rec.best_timing)}",
    ]
    if rec.technique:
        lines.append("\n<b>Technique</b>")
        lines.extend(f"• {_e(item)}" for item in rec.technique[:4])
    if rec.warnings:
        lines.append("\n<b>Watch out</b>")
        lines.extend(f"• {_e(item)}" for item in rec.warnings[:3])
    if rec.reasoning:
        lines.append("\n<b>Why Gemini chose this</b>")
        lines.extend(f"• {_e(item)}" for item in rec.reasoning[:3])
    lines.append(f"\n<i>Gemini model: {_e(rec.model_used)}</i>")
    text = "\n".join(lines)
    return text if len(text) <= 3900 else text[:3890] + "…"
