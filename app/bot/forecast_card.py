"""Deterministic Plane Alerts forecast-card renderer for Telegram.

This module deliberately uses ordinary vector-like drawing with Pillow. It does
not call an image model or any external service, so the image always represents
exactly the same forecast data shown in text.
"""
from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
from typing import Any

from PIL import Image, ImageDraw, ImageFont

WIDTH = 1080
HEIGHT = 1350
BG = "#0E141B"
PANEL = "#171F29"
PANEL_2 = "#111821"
TEXT = "#F4F1E8"
MUTED = "#9AA7B5"
GRID = "#2B3744"
BLUE = "#7CC4FF"
GREEN = "#8ED6A7"
AMBER = "#F1C777"
RED = "#F39A8A"


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    names = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
    ]
    for name in names:
        try:
            return ImageFont.truetype(name, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _aware(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _minutes(now: datetime, doc: dict[str, Any]) -> int | None:
    value = _aware(doc.get("predicted_cpa_at"))
    if value is None:
        return None
    return max(0, int(round((value - now).total_seconds() / 60.0)))


def _bucket(minutes: int | None) -> str:
    if minutes is None or minutes <= 15:
        return "0–15"
    if minutes <= 30:
        return "15–30"
    return "30–60"


def _short_callsign(doc: dict[str, Any]) -> str:
    value = str(doc.get("callsign") or doc.get("aircraft_icao24") or "UNKNOWN").strip().upper()
    return value[:16]


def _distance(doc: dict[str, Any]) -> str:
    try:
        return f"{float(doc.get('predicted_closest_km')):.1f} km"
    except (TypeError, ValueError):
        return "distance unknown"


def _confidence(doc: dict[str, Any]) -> str:
    return str(doc.get("confidence") or "Low").strip().title()


def _source_label(doc: dict[str, Any]) -> str:
    return "LIVE CPA" if doc.get("source") == "live" else "HISTORY SHADOW"


def _source_color(doc: dict[str, Any]) -> str:
    return GREEN if doc.get("source") == "live" else AMBER


def _rounded(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], radius: int, fill: str, outline: str | None = None, width: int = 1) -> None:
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def _plane_icon(draw: ImageDraw.ImageDraw, x: int, y: int, color: str, scale: float = 1.0) -> None:
    # Compact top-down aircraft glyph drawn from simple polygons.
    s = scale
    body = [(x, y - int(16*s)), (x + int(4*s), y + int(12*s)), (x, y + int(20*s)), (x - int(4*s), y + int(12*s))]
    wings = [(x - int(22*s), y + int(2*s)), (x - int(3*s), y - int(3*s)), (x + int(22*s), y + int(2*s)), (x + int(3*s), y + int(7*s))]
    tail = [(x - int(9*s), y + int(13*s)), (x, y + int(9*s)), (x + int(9*s), y + int(13*s)), (x, y + int(17*s))]
    draw.polygon(wings, fill=color)
    draw.polygon(body, fill=color)
    draw.polygon(tail, fill=color)


def _draw_header(draw: ImageDraw.ImageDraw, generated_at: datetime) -> None:
    draw.text((68, 62), "PLANE ALERTS", font=_font(24, True), fill=BLUE)
    draw.text((68, 100), "Next 60 Minutes", font=_font(58, True), fill=TEXT)
    draw.text((70, 172), "Live geometry first · Prediction Lab fills the longer horizon", font=_font(24), fill=MUTED)

    _rounded(draw, (770, 67, 1010, 126), 20, PANEL, GRID)
    draw.text((794, 83), "v4.0  LIVE + SHADOW", font=_font(18, True), fill=TEXT)
    draw.text((782, 150), generated_at.astimezone(timezone.utc).strftime("%H:%M UTC"), font=_font(19), fill=MUTED)


def _draw_timeline(draw: ImageDraw.ImageDraw) -> None:
    x = 112
    y0 = 300
    y1 = 1204
    draw.line((x, y0, x, y1), fill=GRID, width=4)
    for minute, y in ((0, y0), (15, 520), (30, 744), (60, y1)):
        draw.ellipse((x-7, y-7, x+7, y+7), fill=TEXT if minute == 0 else GRID)
        draw.text((54, y-16), str(minute), font=_font(18, True), fill=MUTED)
    draw.text((51, 1230), "MIN", font=_font(16, True), fill=MUTED)


def _row(draw: ImageDraw.ImageDraw, doc: dict[str, Any], now: datetime, x: int, y: int, w: int, h: int) -> None:
    _rounded(draw, (x, y, x+w, y+h), 24, PANEL_2, GRID)
    source_color = _source_color(doc)
    draw.rectangle((x, y, x+8, y+h), fill=source_color)

    _plane_icon(draw, x+48, y+48, source_color, 0.72)
    draw.text((x+82, y+22), _short_callsign(doc), font=_font(27, True), fill=TEXT)

    mins = _minutes(now, doc)
    timing = "timing unavailable" if mins is None else ("now" if mins == 0 else f"~{mins} min")
    draw.text((x+82, y+59), timing, font=_font(21), fill=BLUE if doc.get("source") == "live" else TEXT)

    draw.text((x+w-250, y+24), _distance(doc), font=_font(22, True), fill=TEXT)
    draw.text((x+w-250, y+58), _confidence(doc), font=_font(18), fill=MUTED)

    label = _source_label(doc)
    bbox = draw.textbbox((0, 0), label, font=_font(14, True))
    chip_w = bbox[2] - bbox[0] + 30
    _rounded(draw, (x+w-chip_w-24, y+h-39, x+w-24, y+h-10), 13, PANEL, source_color)
    draw.text((x+w-chip_w-9, y+h-34), label, font=_font(14, True), fill=source_color)


def _draw_section(draw: ImageDraw.ImageDraw, title: str, docs: list[dict[str, Any]], now: datetime, top: int, height: int) -> None:
    x = 170
    w = 840
    _rounded(draw, (x, top, x+w, top+height), 30, PANEL, GRID, 2)
    draw.text((x+32, top+24), title, font=_font(27, True), fill=TEXT)

    if not docs:
        draw.text((x+32, top+74), "No current candidates", font=_font(22), fill=MUTED)
        return

    max_rows = 3
    row_h = 104
    y = top + 68
    for doc in docs[:max_rows]:
        _row(draw, doc, now, x+24, y, w-48, row_h)
        y += row_h + 14

    extra = len(docs) - max_rows
    if extra > 0:
        draw.text((x+32, top+height-38), f"+{extra} more in Telegram text", font=_font(17), fill=MUTED)


def render_forecast_card(now: datetime, docs: list[dict[str, Any]]) -> BytesIO:
    """Return a Telegram-ready PNG containing the current Next 60 forecast."""
    now = now.astimezone(timezone.utc)
    image = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(image)

    _draw_header(draw, now)
    _draw_timeline(draw)

    buckets: dict[str, list[dict[str, Any]]] = {"0–15": [], "15–30": [], "30–60": []}
    for doc in docs:
        mins = _minutes(now, doc)
        if mins is None or mins > 60:
            continue
        buckets[_bucket(mins)].append(doc)
    for values in buckets.values():
        values.sort(key=lambda d: _aware(d.get("predicted_cpa_at")) or now)

    _draw_section(draw, "0–15 MIN · LIVE WINDOW", buckets["0–15"], now, 264, 300)
    _draw_section(draw, "15–30 MIN · DEVELOPING", buckets["15–30"], now, 586, 300)
    _draw_section(draw, "30–60 MIN · SHADOW WINDOW", buckets["30–60"], now, 908, 300)

    draw.line((70, 1268, 1010, 1268), fill=GRID, width=2)
    draw.text((70, 1292), "Live CPA is authoritative when available. 30–60 minute history remains shadow-only.", font=_font(17), fill=MUTED)

    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    output.seek(0)
    output.name = "plane-alerts-next60.png"
    return output
