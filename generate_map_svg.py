#!/usr/bin/env python3
"""Generate an SVG map of Brno-střed electoral precincts with numbers."""

from __future__ import annotations

import argparse
import json
import math
import urllib.parse
import urllib.request
from pathlib import Path
from xml.sax.saxutils import escape

GEOJSON_URL = (
    "https://ags.cuzk.gov.cz/arcgis/rest/services/RUIAN/MapServer/20/query?"
    + urllib.parse.urlencode(
        {
            "where": "momc=550973",
            "outFields": "kod,cislo,momc,obec",
            "outSR": "5514",
            "returnGeometry": "true",
            "f": "geojson",
        }
    )
)

WIDTH = 1600
PADDING = 56
TITLE = "Brno-střed — volební okrsky"


def download_geojson(path: Path) -> dict:
    request = urllib.request.Request(GEOJSON_URL, headers={"User-Agent": "elections-brno/1.0"})
    with urllib.request.urlopen(request, timeout=60) as response:
        path.write_bytes(response.read())
    return json.loads(path.read_text(encoding="utf-8"))


def load_geojson(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    path.parent.mkdir(parents=True, exist_ok=True)
    return download_geojson(path)


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


def generate_svg(features: list[dict]) -> str:
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

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {height:.1f}" width="{WIDTH}" height="{height:.0f}">
  <title>{escape(TITLE)}</title>
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
  <text class="title" x="{PADDING}" y="18">{escape(TITLE)}</text>
  <text class="subtitle" x="{PADDING}" y="48">70 okrsků · čísla 23001–23070</text>
  <g fill-rule="evenodd">
{chr(10).join(paths)}
  </g>
  <g>
{chr(10).join(labels)}
  </g>
</svg>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Vygeneruje SVG mapu okrsků Brno-střed.")
    parser.add_argument(
        "--geojson",
        type=Path,
        default=Path(__file__).resolve().parent / "data" / "okrsky-brno-stred.geojson",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).resolve().parent / "output" / "okrsky.svg",
    )
    args = parser.parse_args()
    data = load_geojson(args.geojson)
    features = collect_features(data)
    if len(features) != 70:
        raise SystemExit(f"Očekáváno 70 okrsků, nalezeno {len(features)}")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(generate_svg(features), encoding="utf-8")
    print(f"SVG: {args.out} ({args.out.stat().st_size} B, {len(features)} okrsků)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
