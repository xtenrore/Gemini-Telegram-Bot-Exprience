"""Telegram-native satellite map renderer for Plane? v3.7.

This replaces the browser live-map page. The renderer consumes aircraft positions
already present in the shared ADS-B monitor, fetches only map imagery, and returns
a JPEG that can be sent/edited directly inside Telegram.
"""
from __future__ import annotations

import asyncio
import math
import time
from collections import OrderedDict
from dataclasses import dataclass
from io import BytesIO
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from app.aircraft.providers import get_http_client

_CANVAS = 640
_TILE_SIZE = 256
_TILE_CACHE_MAX = 128
_TILE_OK_TTL_S = 3600.0
_TILE_FAIL_TTL_S = 30.0
_TRACK_CACHE_MAX = 512
_TRACK_POINTS_MAX = 24
_TILE_URL = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"

_tile_cache: OrderedDict[tuple[int, int, int], tuple[float, bytes | None]] = OrderedDict()
_tracks: OrderedDict[str, list[tuple[float, float, float]]] = OrderedDict()


@dataclass(frozen=True)
class AircraftShape:
    length_m: float
    wingspan_m: float
    engines: int
    kind: str = "jet"


# Common ICAO type designators. Family fallbacks below cover types not listed.
_SPECS: dict[str, AircraftShape] = {
    # Airbus four-engine / very large
    "A388": AircraftShape(72.7, 79.8, 4),
    "A380": AircraftShape(72.7, 79.8, 4),
    "A346": AircraftShape(75.4, 63.5, 4),
    "A345": AircraftShape(67.9, 63.5, 4),
    "A343": AircraftShape(63.7, 60.3, 4),
    "A342": AircraftShape(59.4, 60.3, 4),
    # Boeing four-engine
    "B748": AircraftShape(76.3, 68.4, 4),
    "B744": AircraftShape(70.7, 64.4, 4),
    "B743": AircraftShape(70.7, 59.6, 4),
    "B742": AircraftShape(70.7, 59.6, 4),
    "B741": AircraftShape(70.7, 59.6, 4),
    # Airbus widebody twins
    "A35K": AircraftShape(73.8, 64.8, 2),
    "A359": AircraftShape(66.8, 64.8, 2),
    "A339": AircraftShape(63.7, 64.0, 2),
    "A338": AircraftShape(58.8, 64.0, 2),
    "A333": AircraftShape(63.7, 60.3, 2),
    "A332": AircraftShape(58.8, 60.3, 2),
    "A310": AircraftShape(46.7, 43.9, 2),
    "A306": AircraftShape(54.1, 44.8, 2),
    # Boeing widebody twins
    "B77W": AircraftShape(73.9, 64.8, 2),
    "B77L": AircraftShape(63.7, 64.8, 2),
    "B773": AircraftShape(73.9, 60.9, 2),
    "B772": AircraftShape(63.7, 60.9, 2),
    "B78X": AircraftShape(68.3, 60.1, 2),
    "B789": AircraftShape(62.8, 60.1, 2),
    "B788": AircraftShape(56.7, 60.1, 2),
    "B764": AircraftShape(61.4, 51.9, 2),
    "B763": AircraftShape(54.9, 47.6, 2),
    "B762": AircraftShape(48.5, 47.6, 2),
    # Airbus narrowbody
    "A21N": AircraftShape(44.5, 35.8, 2),
    "A321": AircraftShape(44.5, 35.8, 2),
    "A20N": AircraftShape(37.6, 35.8, 2),
    "A320": AircraftShape(37.6, 35.8, 2),
    "A319": AircraftShape(33.8, 35.8, 2),
    "A318": AircraftShape(31.4, 34.1, 2),
    "BCS3": AircraftShape(38.7, 35.1, 2),
    "BCS1": AircraftShape(35.0, 35.1, 2),
    # Boeing narrowbody
    "B39M": AircraftShape(42.2, 35.9, 2),
    "B38M": AircraftShape(39.5, 35.9, 2),
    "B37M": AircraftShape(35.6, 35.9, 2),
    "B739": AircraftShape(42.1, 35.8, 2),
    "B738": AircraftShape(39.5, 35.8, 2),
    "B737": AircraftShape(33.4, 28.9, 2),
    "B736": AircraftShape(31.2, 34.3, 2),
    "B752": AircraftShape(47.3, 38.1, 2),
    "B753": AircraftShape(54.5, 38.1, 2),
    # Embraer / regional jets
    "E295": AircraftShape(41.5, 35.1, 2),
    "E290": AircraftShape(36.2, 35.1, 2),
    "E195": AircraftShape(38.7, 28.7, 2),
    "E190": AircraftShape(36.2, 28.7, 2),
    "E175": AircraftShape(31.7, 26.0, 2),
    "E170": AircraftShape(29.9, 26.0, 2),
    "CRJX": AircraftShape(39.1, 26.2, 2),
    "CRJ9": AircraftShape(36.4, 24.9, 2),
    "CRJ7": AircraftShape(32.5, 23.2, 2),
    "CRJ2": AircraftShape(26.8, 21.2, 2),
    # Turboprops
    "AT76": AircraftShape(27.2, 27.1, 2, "turboprop"),
    "AT75": AircraftShape(27.2, 27.1, 2, "turboprop"),
    "AT72": AircraftShape(27.2, 27.1, 2, "turboprop"),
    "AT46": AircraftShape(22.7, 24.6, 2, "turboprop"),
    "AT45": AircraftShape(22.7, 24.6, 2, "turboprop"),
    "DH8D": AircraftShape(32.8, 28.4, 2, "turboprop"),
    "DH8C": AircraftShape(25.7, 27.4, 2, "turboprop"),
    # Business jets
    "GLEX": AircraftShape(30.3, 28.7, 2, "bizjet"),
    "GL7T": AircraftShape(33.8, 31.7, 2, "bizjet"),
    "GL6T": AircraftShape(30.3, 31.7, 2, "bizjet"),
    "GL5T": AircraftShape(29.5, 28.5, 2, "bizjet"),
    "C700": AircraftShape(22.3, 21.0, 2, "bizjet"),
    "C68A": AircraftShape(21.0, 21.0, 2, "bizjet"),
    "C56X": AircraftShape(16.0, 17.2, 2, "bizjet"),
    "FA8X": AircraftShape(24.5, 26.3, 3, "bizjet"),
    "FA7X": AircraftShape(23.2, 26.2, 3, "bizjet"),
}


def _spec_for_type(aircraft_type: str | None) -> AircraftShape:
    code = str(aircraft_type or "").upper().strip()
    if code in _SPECS:
        return _SPECS[code]
    if code.startswith("A38"):
        return _SPECS["A388"]
    if code.startswith("B74"):
        return _SPECS["B748"]
    if code.startswith("A34"):
        return _SPECS["A346"]
    if code.startswith(("A35", "B77", "B78", "A33")):
        return AircraftShape(66.0, 63.0, 2)
    if code.startswith(("A32", "A31", "B73", "BCS")):
        return AircraftShape(39.0, 35.5, 2)
    if code.startswith(("E1", "E2", "CRJ")):
        return AircraftShape(35.0, 28.0, 2)
    if code.startswith(("AT", "DH8")):
        return AircraftShape(28.0, 28.0, 2, "turboprop")
    if code.startswith(("GL", "C5", "C6", "C7", "FA")):
        return AircraftShape(24.0, 24.0, 2, "bizjet")
    if code.startswith(("H", "EC", "AS", "S76")):
        return AircraftShape(14.0, 14.0, 1, "helicopter")
    return AircraftShape(38.0, 34.0, 2)


def _icon_size_px(spec: AircraftShape) -> int:
    # Larger real aircraft get larger map symbols, but the range remains usable
    # on a phone display and does not imply literal map scale.
    return max(28, min(54, int(round(22 + spec.wingspan_m * 0.38))))


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def _world_pixel(lat: float, lon: float, zoom: int) -> tuple[float, float]:
    lat = max(-85.05112878, min(85.05112878, float(lat)))
    scale = (2 ** zoom) * _TILE_SIZE
    x = (float(lon) + 180.0) / 360.0 * scale
    s = math.sin(math.radians(lat))
    y = (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * scale
    return x, y


def _choose_zoom(observer_lat: float, distance_km: float) -> int:
    wanted_half_km = max(3.0, float(distance_km) * 1.32 + 1.0)
    for zoom in range(14, 5, -1):
        metres_per_px = 156543.03392 * math.cos(math.radians(observer_lat)) / (2 ** zoom)
        if (_CANVAS / 2) * metres_per_px / 1000.0 >= wanted_half_km:
            return zoom
    return 5


def _cache_tile(key: tuple[int, int, int], data: bytes | None) -> None:
    ttl = _TILE_OK_TTL_S if data else _TILE_FAIL_TTL_S
    _tile_cache[key] = (time.monotonic() + ttl, data)
    _tile_cache.move_to_end(key)
    while len(_tile_cache) > _TILE_CACHE_MAX:
        _tile_cache.popitem(last=False)


async def _get_tile(zoom: int, x: int, y: int) -> bytes | None:
    n = 2 ** zoom
    if y < 0 or y >= n:
        return None
    x = x % n
    key = (zoom, x, y)
    cached = _tile_cache.get(key)
    now = time.monotonic()
    if cached and cached[0] > now:
        _tile_cache.move_to_end(key)
        return cached[1]
    if cached:
        _tile_cache.pop(key, None)

    try:
        client = await get_http_client()
        response = await asyncio.wait_for(
            client.get(_TILE_URL.format(z=zoom, y=y, x=x)), timeout=1.8
        )
        if response.status_code == 200 and response.content:
            data = bytes(response.content)
            _cache_tile(key, data)
            return data
    except Exception:
        pass
    _cache_tile(key, None)
    return None


def _track_for(icao24: str, lat: float, lon: float) -> list[tuple[float, float, float]]:
    key = str(icao24 or "unknown").lower()
    now = time.monotonic()
    points = _tracks.get(key, [])
    if not points or _haversine_km(points[-1][1], points[-1][2], lat, lon) >= 0.05 or now - points[-1][0] >= 3.0:
        points = [*points, (now, float(lat), float(lon))][- _TRACK_POINTS_MAX:]
    _tracks[key] = points
    _tracks.move_to_end(key)
    while len(_tracks) > _TRACK_CACHE_MAX:
        _tracks.popitem(last=False)
    return points


def _draw_dashed(draw: ImageDraw.ImageDraw, points: list[tuple[float, float]], fill: tuple[int, int, int, int], width: int = 3) -> None:
    if len(points) < 2:
        return
    for a, b in zip(points, points[1:]):
        x1, y1 = a
        x2, y2 = b
        dx, dy = x2 - x1, y2 - y1
        length = max(1.0, math.hypot(dx, dy))
        ux, uy = dx / length, dy / length
        pos = 0.0
        while pos < length:
            end = min(length, pos + 9.0)
            draw.line((x1 + ux * pos, y1 + uy * pos, x1 + ux * end, y1 + uy * end), fill=fill, width=width)
            pos += 16.0


def _aircraft_icon(aircraft_type: str | None, heading_deg: float | None) -> Image.Image:
    spec = _spec_for_type(aircraft_type)
    target = _icon_size_px(spec)
    canvas = max(54, target + 18)
    image = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    cx = canvas / 2
    max_dim = max(spec.length_m, spec.wingspan_m)
    body_len = target * 0.88 * spec.length_m / max_dim
    wing_span = target * 0.92 * spec.wingspan_m / max_dim
    top = cx - body_len / 2
    bottom = cx + body_len / 2
    wing_y = cx - body_len * 0.04
    fuselage_w = max(4.0, target * (0.105 if spec.kind != "bizjet" else 0.09))
    fill = (247, 250, 252, 255)
    edge = (8, 16, 24, 255)

    if spec.kind == "helicopter":
        draw.ellipse((cx - 6, cx - 9, cx + 6, cx + 7), fill=fill, outline=edge, width=2)
        draw.polygon([(cx - 2, cx + 4), (cx + 2, cx + 4), (cx + 3, bottom), (cx - 3, bottom)], fill=fill, outline=edge)
        rotor = wing_span / 2
        draw.line((cx - rotor, cx - 2, cx + rotor, cx - 2), fill=edge, width=2)
        draw.line((cx, cx - 2 - rotor, cx, cx - 2 + rotor), fill=edge, width=2)
    else:
        # Main swept wing.
        sweep = body_len * (0.11 if spec.kind == "jet" else 0.06)
        root = fuselage_w / 2
        left = [
            (cx - root, wing_y - 3),
            (cx - wing_span / 2, wing_y + sweep),
            (cx - wing_span * 0.34, wing_y + sweep + max(2, target * 0.06)),
            (cx - root, wing_y + 5),
        ]
        right = [(2 * cx - x, y) for x, y in left]
        draw.polygon(left, fill=fill, outline=edge)
        draw.polygon(right, fill=fill, outline=edge)

        # Tailplane proportions differ a little for business jets.
        tail_y = bottom - body_len * 0.18
        tail_span = wing_span * (0.34 if spec.kind != "bizjet" else 0.38)
        draw.polygon(
            [
                (cx - fuselage_w / 2, tail_y - 2),
                (cx - tail_span / 2, tail_y + 4),
                (cx - tail_span * 0.28, tail_y + 7),
                (cx - fuselage_w / 2, tail_y + 4),
            ],
            fill=fill,
            outline=edge,
        )
        draw.polygon(
            [
                (cx + fuselage_w / 2, tail_y - 2),
                (cx + tail_span / 2, tail_y + 4),
                (cx + tail_span * 0.28, tail_y + 7),
                (cx + fuselage_w / 2, tail_y + 4),
            ],
            fill=fill,
            outline=edge,
        )

        # Fuselage on top of the wings.
        draw.polygon(
            [
                (cx, top),
                (cx + fuselage_w * 0.55, top + body_len * 0.12),
                (cx + fuselage_w * 0.52, bottom - body_len * 0.12),
                (cx + fuselage_w * 0.30, bottom),
                (cx - fuselage_w * 0.30, bottom),
                (cx - fuselage_w * 0.52, bottom - body_len * 0.12),
                (cx - fuselage_w * 0.55, top + body_len * 0.12),
            ],
            fill=fill,
            outline=edge,
        )

        # Engines. Four-engine types such as A380/B747 visibly get four nacelles.
        if spec.kind == "bizjet":
            engine_positions = [-0.12, 0.12] if spec.engines == 2 else [-0.16, 0.0, 0.16]
            engine_y = bottom - body_len * 0.28
            for frac in engine_positions:
                ex = cx + wing_span * frac
                draw.ellipse((ex - 2.3, engine_y - 5, ex + 2.3, engine_y + 4), fill=(205, 214, 221, 255), outline=edge)
        else:
            if spec.engines >= 4:
                engine_positions = [-0.34, -0.18, 0.18, 0.34]
            elif spec.engines == 3:
                engine_positions = [-0.24, 0.0, 0.24]
            else:
                engine_positions = [-0.27, 0.27]
            engine_y = wing_y + max(2.0, body_len * 0.07)
            for frac in engine_positions:
                ex = cx + wing_span * frac
                ew = max(2.2, target * 0.045)
                eh = max(4.6, target * 0.095)
                draw.ellipse((ex - ew, engine_y - eh / 2, ex + ew, engine_y + eh / 2), fill=(207, 216, 223, 255), outline=edge)
                if spec.kind == "turboprop":
                    draw.line((ex - 6, engine_y - eh / 2 - 1, ex + 6, engine_y - eh / 2 - 1), fill=edge, width=1)

    # A small shadow keeps the silhouette readable over light satellite imagery.
    alpha = image.getchannel("A")
    shadow = Image.new("RGBA", image.size, (0, 0, 0, 0))
    shadow.putalpha(alpha.filter(ImageFilter.GaussianBlur(2.0)))
    shadow_color = Image.new("RGBA", image.size, (0, 0, 0, 130))
    shadow_color.putalpha(shadow.getchannel("A"))
    merged = Image.new("RGBA", image.size, (0, 0, 0, 0))
    merged.alpha_composite(shadow_color, (1, 2))
    merged.alpha_composite(image)

    heading = float(heading_deg or 0.0) % 360.0
    return merged.rotate(-heading, resample=Image.Resampling.BICUBIC, expand=True)


def _prediction_points(prediction: Any) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for point in list(getattr(prediction, "path", None) or [])[:18]:
        try:
            if isinstance(point, dict):
                lat = float(point.get("latitude", point.get("lat")))
                lon = float(point.get("longitude", point.get("lon")))
            else:
                lat = float(point.latitude)
                lon = float(point.longitude)
            out.append((lat, lon))
        except (TypeError, ValueError, AttributeError):
            continue
    return out


async def render_telegram_map(
    observer_lat: float,
    observer_lon: float,
    aircraft: Any,
    prediction: Any | None = None,
) -> bytes:
    """Render one 640px satellite JPEG for an aircraft alert.

    The aircraft position is passed directly from the shared monitor. There is no
    waiting/polling state and no extra ADS-B provider request.
    """
    if getattr(aircraft, "latitude", None) is None or getattr(aircraft, "longitude", None) is None:
        raise ValueError("aircraft position unavailable")

    alat = float(aircraft.latitude)
    alon = float(aircraft.longitude)
    distance = _haversine_km(float(observer_lat), float(observer_lon), alat, alon)
    zoom = _choose_zoom(float(observer_lat), distance)
    center_x, center_y = _world_pixel(observer_lat, observer_lon, zoom)
    left = center_x - _CANVAS / 2
    top = center_y - _CANVAS / 2
    x0 = math.floor(left / _TILE_SIZE)
    x1 = math.floor((left + _CANVAS - 1) / _TILE_SIZE)
    y0 = math.floor(top / _TILE_SIZE)
    y1 = math.floor((top + _CANVAS - 1) / _TILE_SIZE)

    keys = [(x, y) for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)]
    tile_data = await asyncio.gather(*(_get_tile(zoom, x, y) for x, y in keys))

    image = Image.new("RGB", (_CANVAS, _CANVAS), (31, 42, 52))
    for (tx, ty), data in zip(keys, tile_data):
        if not data:
            continue
        try:
            tile = Image.open(BytesIO(data)).convert("RGB")
            px = int(round(tx * _TILE_SIZE - left))
            py = int(round(ty * _TILE_SIZE - top))
            image.paste(tile, (px, py))
        except Exception:
            continue

    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    def screen(lat: float, lon: float) -> tuple[float, float]:
        wx, wy = _world_pixel(lat, lon, zoom)
        return wx - left, wy - top

    user_xy = screen(observer_lat, observer_lon)
    plane_xy = screen(alat, alon)

    # Recent path is collected only from alerts that were already processed.
    track = _track_for(str(getattr(aircraft, "icao24", "")), alat, alon)
    track_xy = [screen(lat, lon) for _, lat, lon in track]
    if len(track_xy) >= 2:
        draw.line(track_xy, fill=(255, 205, 74, 220), width=4, joint="curve")
        for x, y in track_xy[:-1]:
            draw.ellipse((x - 2, y - 2, x + 2, y + 2), fill=(255, 220, 110, 210))

    # Line from the user's blue point to the aircraft.
    _draw_dashed(draw, [user_xy, plane_xy], (255, 255, 255, 205), width=3)

    projected = _prediction_points(prediction) if prediction is not None else []
    projected_xy = [screen(lat, lon) for lat, lon in projected]
    if len(projected_xy) >= 2:
        _draw_dashed(draw, projected_xy, (76, 202, 255, 210), width=3)

    ux, uy = user_xy
    draw.ellipse((ux - 16, uy - 16, ux + 16, uy + 16), fill=(20, 137, 255, 55))
    draw.ellipse((ux - 9, uy - 9, ux + 9, uy + 9), fill=(20, 137, 255, 255), outline=(255, 255, 255, 255), width=3)

    icon = _aircraft_icon(getattr(aircraft, "aircraft_type", None), getattr(aircraft, "heading", None))
    px, py = plane_xy
    overlay.paste(icon, (int(round(px - icon.width / 2)), int(round(py - icon.height / 2))), icon)

    # Compact info strip. The detailed alert stays in the Telegram caption.
    callsign = str(getattr(aircraft, "callsign", "") or "").strip()
    typ = str(getattr(aircraft, "aircraft_type", "") or "Aircraft").strip()
    altitude = getattr(aircraft, "altitude", None)
    speed = getattr(aircraft, "ground_speed", None)
    title = f"{callsign + ' · ' if callsign else ''}{typ}"
    detail_parts = [f"{distance:.1f} km"]
    if altitude is not None:
        detail_parts.append(f"{int(round(float(altitude) * 3.28084)):,} ft")
    if speed is not None:
        detail_parts.append(f"{int(round(float(speed)))} kt")
    font = ImageFont.load_default()
    draw.rounded_rectangle((14, 14, 330, 66), radius=10, fill=(5, 12, 18, 190), outline=(255, 255, 255, 45))
    draw.text((26, 24), title[:42], fill=(255, 255, 255, 255), font=font)
    draw.text((26, 44), " · ".join(detail_parts), fill=(205, 220, 232, 255), font=font)

    attribution = "Satellite: Esri, Maxar, Earthstar Geographics, GIS User Community"
    box = draw.textbbox((0, 0), attribution, font=font)
    tw = box[2] - box[0]
    draw.rectangle((_CANVAS - tw - 14, _CANVAS - 22, _CANVAS - 6, _CANVAS - 5), fill=(0, 0, 0, 150))
    draw.text((_CANVAS - tw - 10, _CANVAS - 19), attribution, fill=(235, 235, 235, 235), font=font)

    image = Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")
    output = BytesIO()
    image.save(output, format="JPEG", quality=84, optimize=True)
    return output.getvalue()
