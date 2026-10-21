#!/usr/bin/env python3
"""Generate an SVG map of electoral precincts with numbers."""

from __future__ import annotations

import argparse
import json
import math
import urllib.parse
import urllib.request
from pathlib import Path
from xml.sax.saxutils import escape

from uzemi import Cast, add_uzemi_args, load_uzemi, resolve_cast

WIDTH = 1600
PADDING = 56


def okrsky_url(kod_zastupitelstva: int) -> str:
    return (
        "https://ags.cuzk.gov.cz/arcgis/rest/services/RUIAN/MapServer/20/query?"
        + urllib.parse.urlencode(
            {
                "where": f"momc={kod_zastupitelstva}",
                "outFields": "kod,cislo,momc,obec",
                "outSR": "5514",
                "returnGeometry": "true",
                "f": "geojson",
            }
        )
    )


def katastry_url(obec: int) -> str:
    return (
        "https://ags.cuzk.gov.cz/arcgis/rest/services/RUIAN/MapServer/7/query?"
        + urllib.parse.urlencode(
            {
                "where": f"obec={obec}",
                "outFields": "kod,nazev,obec",
                "outSR": "5514",
                "returnGeometry": "true",
                "f": "geojson",
            }
        )
    )


def download_geojson(path: Path, kod_zastupitelstva: int) -> dict:
    request = urllib.request.Request(
        okrsky_url(kod_zastupitelstva),
        headers={"User-Agent": "elections-brno/1.0"},
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(request, timeout=60) as response:
        path.write_bytes(response.read())
    return json.loads(path.read_text(encoding="utf-8"))


def load_geojson(path: Path, kod_zastupitelstva: int | None = None) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    if kod_zastupitelstva is None:
        raise FileNotFoundError(path)
    return download_geojson(path, kod_zastupitelstva)


def download_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "elections-brno/1.0"})
    with urllib.request.urlopen(request, timeout=90) as response:
        return json.loads(response.read().decode("utf-8"))


def point_in_ring(x: float, y: float, ring: list[list[float]]) -> bool:
    inside = False
    previous = ring[-1]
    for current in ring:
        x1, y1 = previous
        x2, y2 = current
        if (y1 > y) != (y2 > y):
            at_x = x1 + (x2 - x1) * (y - y1) / (y2 - y1)
            if x < at_x:
                inside = not inside
        previous = current
    return inside


def point_in_geometry(x: float, y: float, geometry: dict) -> bool:
    for ring in exterior_rings(geometry):
        if point_in_ring(x, y, ring):
            return True
    return False


def load_katastry(
    path: Path,
    features: list[dict] | None = None,
    obec: int = 582786,
) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    data = download_json(katastry_url(obec))
    if features:
        used = set()
        for item in features:
            cx, cy = item["centroid"]
            for katastr in data["features"]:
                if point_in_geometry(cx, cy, katastr["geometry"]):
                    used.add(katastr["properties"]["kod"])
                    break
        data = {
            "type": "FeatureCollection",
            "crs": data.get("crs")
            or {"type": "name", "properties": {"name": "EPSG:5514"}},
            "features": [
                item
                for item in data["features"]
                if item["properties"]["kod"] in used
            ],
        }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return data


def neighborhood_labels(
    features: list[dict],
    katastry: dict,
    popisky: dict[str, str] | None = None,
    posun: dict[str, tuple[float, float]] | None = None,
) -> list[dict]:
    groups: dict[str, list[dict]] = {}
    for item in features:
        cx, cy = item["centroid"]
        hit = None
        for katastr in katastry["features"]:
            if point_in_geometry(cx, cy, katastr["geometry"]):
                hit = katastr["properties"]["nazev"]
                break
        if hit is None:
            continue
        groups.setdefault(hit, []).append(item)

    labels = []
    for nazev, items in groups.items():
        area = sum(item["area"] for item in items)
        if area <= 0:
            continue
        x = sum(item["centroid"][0] * item["area"] for item in items) / area
        y = sum(item["centroid"][1] * item["area"] for item in items) / area
        dx, dy = (posun or {}).get(nazev, (0.0, 0.0))
        x += dx
        y += dy
        labels.append(
            {
                "nazev": (popisky or {}).get(nazev, nazev),
                "x": x,
                "y": y,
                "area": area,
            }
        )
    return sorted(labels, key=lambda item: item["area"], reverse=True)


def neighborhood_texts(
    labels: list[dict],
    tx,
    ty,
    min_font: float,
    max_font: float,
) -> list[str]:
    if not labels:
        return []
    max_area = max(item["area"] for item in labels)
    texts = []
    for item in labels:
        t = math.sqrt(item["area"] / max_area) if max_area else 1.0
        font = min_font + (max_font - min_font) * t
        texts.append(
            f'    <text class="cast" x="{tx(item["x"]):.1f}" y="{ty(item["y"]):.1f}" '
            f'font-size="{font:.1f}">{escape(item["nazev"])}</text>'
        )
    return texts


def exterior_rings(geometry: dict) -> list[list[list[float]]]:
    if geometry["type"] == "Polygon":
        return [geometry["coordinates"][0]]
    if geometry["type"] == "MultiPolygon":
        return [polygon[0] for polygon in geometry["coordinates"]]
    return []


def all_rings(geometry: dict) -> list[list[list[float]]]:
    if geometry["type"] == "Polygon":
        return list(geometry["coordinates"])
    if geometry["type"] == "MultiPolygon":
        rings = []
        for polygon in geometry["coordinates"]:
            rings.extend(polygon)
        return rings
    return []


def ring_area(ring: list[list[float]]) -> float:
    area = 0.0
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
        area += x1 * y2 - x2 * y1
    return abs(area) / 2.0


def ring_centroid(ring: list[list[float]]) -> tuple[float, float]:
    points = ring[:-1] if ring[0] == ring[-1] else ring
    area_twice = 0.0
    cx = 0.0
    cy = 0.0
    for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1]):
        cross = x1 * y2 - x2 * y1
        area_twice += cross
        cx += (x1 + x2) * cross
        cy += (y1 + y2) * cross
    if abs(area_twice) < 1e-9:
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        return sum(xs) / len(xs), sum(ys) / len(ys)
    return cx / (3 * area_twice), cy / (3 * area_twice)


def bbox(points: list[tuple[float, float]]) -> tuple[float, float, float, float]:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def collect_features(data: dict) -> list[dict]:
    features = []
    for feature in data["features"]:
        rings = exterior_rings(feature["geometry"])
        if not rings:
            continue
        main = max(rings, key=ring_area)
        cx, cy = ring_centroid(main)
        features.append(
            {
                "cislo": int(feature["properties"]["cislo"]),
                "rings": all_rings(feature["geometry"]),
                "centroid": (cx, cy),
                "area": sum(ring_area(ring) for ring in rings),
            }
        )
    return sorted(features, key=lambda item: item["cislo"])


def path_d(rings: list[list[list[float]]], tx, ty) -> str:
    parts = []
    for ring in rings:
        commands = []
        for index, (x, y) in enumerate(ring):
            prefix = "M" if index == 0 else "L"
            commands.append(f"{prefix}{tx(x):.1f},{ty(y):.1f}")
        commands.append("Z")
        parts.append(" ".join(commands))
    return " ".join(parts)


def generate_svg(
    features: list[dict],
    neighborhoods: list[dict] | None = None,
    title: str = "Volební okrsky",
) -> str:
    points = [(x, y) for item in features for ring in item["rings"] for x, y in ring]
    minx, miny, maxx, maxy = bbox(points)
    span_x = maxx - minx
    span_y = maxy - miny
    inner = WIDTH - 2 * PADDING
    scale = inner / span_x
    height = span_y * scale + 2 * PADDING + 36

    def tx(x: float) -> float:
        return PADDING + (x - minx) * scale

    def ty(y: float) -> float:
        return PADDING + 28 + (maxy - y) * scale

    paths = []
    labels = []
    for item in features:
        number = str(item["cislo"])
        x, y = tx(item["centroid"][0]), ty(item["centroid"][1])
        size_m = math.sqrt(item["area"])
        size_px = size_m * scale
        font = 13 if size_px >= 80 else 11 if size_px >= 50 else 9
        outside = size_px < 48
        label_x = x + (46 if outside else 0)
        label_y = y - (14 if outside else 0)
        if outside:
            labels.append(
                f'    <line x1="{x:.1f}" y1="{y:.1f}" x2="{label_x - 22:.1f}" y2="{label_y:.1f}" class="leader"/>'
            )
            labels.append(
                f'    <rect class="label-bg" x="{label_x - 24:.1f}" y="{label_y - 8:.1f}" width="48" height="16" rx="2"/>'
            )
        paths.append(
            f'    <path class="okrsek" data-okrsek="{number}" d="{path_d(item["rings"], tx, ty)}"/>'
        )
        labels.append(
            f'    <text x="{label_x:.1f}" y="{label_y:.1f}" font-size="{font}">{escape(number)}</text>'
        )
    casti = neighborhood_texts(neighborhoods or [], tx, ty, min_font=17, max_font=28)

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {height:.1f}" width="{WIDTH}" height="{height:.0f}">
  <title>{escape(title)}</title>
  <style>
    .okrsek {{
      fill: #e8eef6;
      stroke: #1e3a5f;
      stroke-width: 1.1;
      stroke-linejoin: round;
    }}
    .okrsek:hover {{ fill: #c9d8ee; }}
    text {{
      font-family: "Source Sans 3", "Liberation Sans", "DejaVu Sans", sans-serif;
      font-weight: 650;
      fill: #0f172a;
      stroke: #ffffff;
      stroke-width: 3.2;
      paint-order: stroke fill;
      text-anchor: middle;
      dominant-baseline: middle;
      pointer-events: none;
    }}
    .leader {{
      stroke: #1e3a5f;
      stroke-width: 1;
    }}
    .label-bg {{
      fill: #ffffff;
      stroke: #1e3a5f;
      stroke-width: 0.8;
    }}
    .cast {{
      font-family: "Source Sans 3", "Liberation Sans", "DejaVu Sans", sans-serif;
      font-weight: 750;
      fill: #1e3a5f;
      fill-opacity: 0.78;
      stroke: #ffffff;
      stroke-width: 5;
      stroke-opacity: 0.9;
      paint-order: stroke fill;
      text-anchor: middle;
      dominant-baseline: middle;
      letter-spacing: 0.4px;
      pointer-events: none;
    }}
    .title {{
      font-family: "Source Sans 3", "Liberation Sans", "DejaVu Sans", sans-serif;
      font-size: 28px;
      font-weight: 700;
      fill: #0f172a;
      stroke: none;
      text-anchor: start;
      dominant-baseline: hanging;
    }}
    .subtitle {{
      font-family: "Source Sans 3", "Liberation Sans", "DejaVu Sans", sans-serif;
      font-size: 14px;
      fill: #475569;
      stroke: none;
      text-anchor: start;
      dominant-baseline: hanging;
    }}
  </style>
  <rect width="100%" height="100%" fill="#f8fafc"/>
  <text class="title" x="{PADDING}" y="18">{escape(title)}</text>
  <text class="subtitle" x="{PADDING}" y="48">{len(features)} okrsků</text>
  <g fill-rule="evenodd">
{chr(10).join(paths)}
  </g>
  <g class="casti">
{chr(10).join(casti)}
  </g>
  <g>
{chr(10).join(labels)}
  </g>
</svg>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Vygeneruje SVG mapu okrsků městské části.")
    add_uzemi_args(parser)
    parser.add_argument("--geojson", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--katastry", type=Path)
    args = parser.parse_args()
    uzemi = load_uzemi()
    cast = resolve_cast(args, uzemi)
    if cast is None:
        return 0
    geojson = args.geojson or cast.geojson
    katastry_path = args.katastry or cast.katastry
    out_path = args.out or (cast.output_dir / "okrsky.svg")
    data = load_geojson(geojson, cast.kod_zastupitelstva)
    features = collect_features(data)
    if not features:
        raise SystemExit(f"V GeoJSON nejsou žádné okrsky: {geojson}")
    neighborhoods = neighborhood_labels(
        features,
        load_katastry(katastry_path, features, uzemi.obec),
        popisky=cast.popisky,
        posun=cast.posun_popisku,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    title = f"{cast.nazev} — volební okrsky"
    out_path.write_text(generate_svg(features, neighborhoods, title), encoding="utf-8")
    print(f"SVG: {out_path} ({out_path.stat().st_size} B, {len(features)} okrsků)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
