#!/usr/bin/env python3
"""SVG choropleth maps of Brno-střed precincts, one panel per party."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from xml.sax.saxutils import escape

from generate_map_svg import (
    PADDING,
    WIDTH,
    bbox,
    collect_features,
    load_geojson,
    load_katastry,
    neighborhood_labels,
    neighborhood_texts,
    path_d,
)
from uzemi import add_uzemi_args, load_uzemi, resolve_cast

PARTY_COLORS = {
    "ANO": (11, 61, 145),
    "ODS_TOP09": (0, 80, 170),
    "KDU_STAN": (196, 146, 20),
    "SPD": (26, 54, 93),
    "PIRATI_FAKT": (234, 88, 12),
    "PIRATI_ZELENI_ZITBRNO": (22, 140, 70),
    "ZELENI_ZITBRNO": (22, 163, 74),
    "BRNOKLIDEM": (71, 85, 105),
    "B3": (147, 51, 234),
    "RESTART": (185, 28, 28),
    "NAPLNO": (8, 145, 178),
    "ZALUZANKY": (190, 24, 93),
    "ZDRAVY_STRED": (202, 138, 4),
    "FAKT": (234, 88, 12),
}

SHORT_NAME = {
    "BRNOKLIDEM": "Brnoklidem",
    "B3": "B3!",
    "ANO": "ANO 2011",
    "SPD": "SPD",
    "KDU_STAN": "Lidovci a Starostové",
    "ZELENI_ZITBRNO": "Žít Zelené Brno",
    "RESTART": "Restart pro Brno",
    "ODS_TOP09": "ODS a TOP 09",
    "PIRATI_FAKT": "Piráti a Fakt Brno",
    "NAPLNO": "NAPLNO",
    "ZALUZANKY": "Za Lužánky",
    "PIRATI_ZELENI_ZITBRNO": "Piráti, Zelení a Žít Brno",
    "ZDRAVY_STRED": "Zdravý střed",
    "FAKT": "Fakt Brno",
}

COLS = 4
PANEL_W = 520
PANEL_H = 560
MAP_PAD = 12
HEADER = 86
FOOTER = 110
GAP = 18
PAGE_PAD = 28


SCALE_STOPS = (
    (0.0, (255, 255, 229)),
    (0.2, (255, 237, 160)),
    (0.4, (254, 178, 76)),
    (0.6, (253, 141, 60)),
    (0.8, (227, 26, 28)),
    (1.0, (128, 0, 38)),
)

# Diverging: pokles (červená) ← 0 (šedá) → nárůst (zlatá, barva KDU).
DIFF_STOPS = (
    (0.0, (178, 24, 43)),
    (0.35, (252, 146, 114)),
    (0.5, (247, 247, 247)),
    (0.65, (254, 217, 118)),
    (1.0, (146, 99, 16)),
)

DIFF_FOOTER = 118
MISSING_FILL = "#cbd5e1"


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def color_from_stops(t: float, stops: tuple, fallback: str) -> str:
    t = max(0.0, min(1.0, t))
    for (t0, c0), (t1, c1) in zip(stops, stops[1:]):
        if t <= t1:
            local = 0 if t1 == t0 else (t - t0) / (t1 - t0)
            rgb = tuple(round(lerp(c0[i], c1[i], local)) for i in range(3))
            return f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"
    return fallback


def scale_color(t: float) -> str:
    return color_from_stops(t, SCALE_STOPS, "#800026")


def diff_color(delta: float, scale_max: float) -> str:
    if scale_max <= 0:
        return "#f7f7f7"
    t = (delta + scale_max) / (2 * scale_max)
    return color_from_stops(t, DIFF_STOPS, "#926310")


def hex_color(rgb: tuple[int, int, int]) -> str:
    return f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"


def party_rgb(kod: str) -> tuple[int, int, int]:
    return PARTY_COLORS.get(kod, (30, 64, 175))


def load_results(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def party_stats(results: dict) -> dict[str, dict]:
    platne = 0
    totals: dict[str, int] = {}
    by_okrsek: dict[str, dict[str, float]] = {}
    for okrsek, record in results["okrsky"].items():
        platne += int(record["prehled"]["platne_hlasy"])
        by_okrsek[str(okrsek)] = {}
        for strana in record["strany"]:
            kod = strana["kod"]
            totals[kod] = totals.get(kod, 0) + int(strana["hlasy"])
            by_okrsek[str(okrsek)][kod] = float(strana["procenta"])
    stats = {}
    for strana in results["strany"]:
        kod = strana["kod"]
        values = [row[kod] for row in by_okrsek.values()]
        stats[kod] = {
            "nazev": strana["nazev"],
            "short": SHORT_NAME.get(kod, strana["nazev"]),
            "overall": 100.0 * totals.get(kod, 0) / platne if platne else 0.0,
            "min": min(values) if values else 0.0,
            "max": max(values) if values else 0.0,
            "by_okrsek": {okrsek: row[kod] for okrsek, row in by_okrsek.items()},
        }
    return stats


def fmt_pct(value: float) -> str:
    text = f"{value:.1f}".replace(".", ",")
    return f"{text} %"


def fmt_delta(value: float, with_unit: bool = False) -> str:
    text = f"{value:+.1f}".replace(".", ",").replace("-", "−")
    return f"{text} p.b." if with_unit else text


def party_pct_by_okrsek(results: dict, kod: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for okrsek, record in results["okrsky"].items():
        pct = 0.0
        for strana in record["strany"]:
            if strana["kod"] == kod:
                pct = float(strana["procenta"])
                break
        out[str(okrsek)] = pct
    return out


def party_overall(results: dict, kod: str) -> float:
    platne = 0
    hlasy = 0
    for record in results["okrsky"].values():
        platne += int(record["prehled"]["platne_hlasy"])
        for strana in record["strany"]:
            if strana["kod"] == kod:
                hlasy += int(strana["hlasy"])
                break
    return 100.0 * hlasy / platne if platne else 0.0


def party_name(results: dict, kod: str) -> str:
    for strana in results["strany"]:
        if strana["kod"] == kod:
            return SHORT_NAME.get(kod, strana["nazev"])
    return SHORT_NAME.get(kod, kod)


def map_transform(features: list[dict], width: float, height: float):
    points = [(x, y) for item in features for ring in item["rings"] for x, y in ring]
    minx, miny, maxx, maxy = bbox(points)
    span_x = maxx - minx
    span_y = maxy - miny
    inner_w = width - 2 * MAP_PAD
    inner_h = height - 2 * MAP_PAD
    scale = min(inner_w / span_x, inner_h / span_y)
    used_w = span_x * scale
    used_h = span_y * scale
    ox = MAP_PAD + (inner_w - used_w) / 2
    oy = MAP_PAD + (inner_h - used_h) / 2

    def tx(x: float) -> float:
        return ox + (x - minx) * scale

    def ty(y: float) -> float:
        return oy + (maxy - y) * scale

    return tx, ty, scale


def legend_gradient(ident: str, stops: tuple | None = None) -> str:
    palette = stops or SCALE_STOPS
    parts = []
    for t, rgb in palette:
        color = f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"
        parts.append(f'<stop offset="{int(t * 100)}%" stop-color="{color}"/>')
    return f'<linearGradient id="{ident}" x1="0" y1="0" x2="1" y2="0">{"".join(parts)}</linearGradient>'


def generate_diff_svg(
    features: list[dict],
    before: dict,
    after: dict,
    kod: str,
    neighborhoods: list[dict] | None = None,
) -> str:
    pct_before = party_pct_by_okrsek(before, kod)
    pct_after = party_pct_by_okrsek(after, kod)
    overall_before = party_overall(before, kod)
    overall_after = party_overall(after, kod)
    name = party_name(after, kod)
    comparable = set(before["okrsky"]) & set(after["okrsky"])
    deltas = {}
    for item in features:
        number = str(item["cislo"])
        if number not in comparable:
            continue
        deltas[number] = pct_after[number] - pct_before[number]
    values = list(deltas.values())
    scale_max = 5 * math.ceil(max(abs(v) for v in values) / 5) if values else 20
    if scale_max <= 0:
        scale_max = 5

    points = [(x, y) for item in features for ring in item["rings"] for x, y in ring]
    minx, miny, maxx, maxy = bbox(points)
    span_x = maxx - minx
    span_y = maxy - miny
    inner = WIDTH - 2 * PADDING
    scale = inner / span_x
    height = span_y * scale + 2 * PADDING + 36 + DIFF_FOOTER

    def tx(x: float) -> float:
        return PADDING + (x - minx) * scale

    def ty(y: float) -> float:
        return PADDING + 28 + (maxy - y) * scale

    paths = []
    labels = []
    for item in features:
        number = str(item["cislo"])
        x, y = tx(item["centroid"][0]), ty(item["centroid"][1])
        size_px = math.sqrt(item["area"]) * scale
        font = 13 if size_px >= 90 else 11 if size_px >= 55 else 9
        outside = size_px < 48
        label_x = x + (52 if outside else 0)
        label_y = y - (18 if outside else 0)
        if outside:
            labels.append(
                f'    <line x1="{x:.1f}" y1="{y:.1f}" x2="{label_x - 24:.1f}" y2="{label_y:.1f}" class="leader"/>'
            )
            labels.append(
                f'    <rect class="label-bg" x="{label_x - 28:.1f}" y="{label_y - 16:.1f}" width="56" height="32" rx="3"/>'
            )
        if number in deltas:
            delta = deltas[number]
            fill = diff_color(delta, scale_max)
            delta_text = fmt_delta(delta)
        else:
            fill = MISSING_FILL
            delta_text = "—"
        paths.append(
            f'    <path class="okrsek" data-okrsek="{number}" d="{path_d(item["rings"], tx, ty)}" fill="{fill}"/>'
        )
        if size_px >= 62 or outside:
            labels.append(
                f'    <text class="okrsek-cislo" x="{label_x:.1f}" y="{label_y - 7:.1f}" '
                f'font-size="{max(font - 2, 8)}">{escape(number)}</text>'
            )
            labels.append(
                f'    <text class="okrsek-delta" x="{label_x:.1f}" y="{label_y + 8:.1f}" '
                f'font-size="{font}">{escape(delta_text)}</text>'
            )
        else:
            labels.append(
                f'    <text class="okrsek-delta" x="{label_x:.1f}" y="{label_y:.1f}" '
                f'font-size="{font}">{escape(delta_text)}</text>'
            )

    casti = neighborhood_texts(neighborhoods or [], tx, ty, min_font=16, max_font=26)
    overall_delta = overall_after - overall_before
    uzemi_nazev = after.get("obec") or before.get("obec") or ""
    title = f"{name} — změna podílu hlasů 2022 → 2026"
    range_text = (
        f" · okrsky {fmt_delta(min(values))} až {fmt_delta(max(values))} p.b."
        if values
        else ""
    )
    subtitle = (
        f"{uzemi_nazev} {fmt_pct(overall_before)} → {fmt_pct(overall_after)} "
        f"({fmt_delta(overall_delta, with_unit=True)}){range_text}"
    )

    ticks = list(range(-int(scale_max), int(scale_max) + 1, 5 if scale_max <= 20 else 10))
    legend_x = PADDING
    legend_y = height - DIFF_FOOTER + 8
    legend_w = WIDTH - 2 * PADDING
    tick_svg = []
    for tick in ticks:
        txp = legend_x + legend_w * ((tick + scale_max) / (2 * scale_max))
        label = f"{tick:+d}".replace("-", "−")
        tick_svg.append(
            f'<line x1="{txp:.1f}" y1="{legend_y + 22}" x2="{txp:.1f}" y2="{legend_y + 30}" stroke="#334155"/>'
        )
        tick_svg.append(
            f'<text class="legend-tick" x="{txp:.1f}" y="{legend_y + 46}">{label}</text>'
        )

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {height:.1f}" width="{WIDTH}" height="{height:.0f}">
  <title>{escape(title)}</title>
  <defs>
    {legend_gradient("diffScale", DIFF_STOPS)}
  </defs>
  <style>
    .okrsek {{
      stroke: #334155;
      stroke-width: 1.05;
      stroke-linejoin: round;
    }}
    text {{
      font-family: "Source Sans 3", "Liberation Sans", "DejaVu Sans", sans-serif;
      paint-order: stroke fill;
      text-anchor: middle;
      pointer-events: none;
    }}
    .okrsek-cislo {{
      font-weight: 600;
      fill: #475569;
      stroke: #ffffff;
      stroke-width: 3;
      dominant-baseline: middle;
    }}
    .okrsek-delta {{
      font-weight: 750;
      fill: #0f172a;
      stroke: #ffffff;
      stroke-width: 3.4;
      dominant-baseline: middle;
    }}
    .leader {{ stroke: #1e3a5f; stroke-width: 1; }}
    .label-bg {{ fill: #ffffff; stroke: #1e3a5f; stroke-width: 0.8; }}
    .cast {{
      font-family: "Source Sans 3", "Liberation Sans", "DejaVu Sans", sans-serif;
      font-weight: 750;
      fill: #1e3a5f;
      fill-opacity: 0.55;
      stroke: #ffffff;
      stroke-width: 5;
      stroke-opacity: 0.75;
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
    .legend-label, .legend-tick {{
      font-family: "Source Sans 3", "Liberation Sans", "DejaVu Sans", sans-serif;
      fill: #334155;
      stroke: none;
    }}
    .legend-label {{ font-size: 14px; font-weight: 650; text-anchor: start; dominant-baseline: hanging; }}
    .legend-tick {{ font-size: 12px; text-anchor: middle; dominant-baseline: hanging; }}
  </style>
  <rect width="100%" height="100%" fill="#f8fafc"/>
  <text class="title" x="{PADDING}" y="18">{escape(title)}</text>
  <text class="subtitle" x="{PADDING}" y="50">{escape(subtitle)}</text>
  <g fill-rule="evenodd">
{chr(10).join(paths)}
  </g>
  <g class="casti">
{chr(10).join(casti)}
  </g>
  <g>
{chr(10).join(labels)}
  </g>
  <text class="legend-label" x="{legend_x}" y="{legend_y}">Legenda: rozdíl podílu hlasů v okrsku (2026 − 2022)</text>
  <rect x="{legend_x}" y="{legend_y + 22}" width="{legend_w}" height="16" fill="url(#diffScale)" stroke="#334155" stroke-width="0.6"/>
  {"".join(tick_svg)}
</svg>
"""


def generate_svg(
    features: list[dict],
    results: dict,
    neighborhoods: list[dict] | None = None,
    nazev: str | None = None,
) -> str:
    stats = party_stats(results)
    parties = [row["kod"] for row in results["strany"]]
    scale_max = 5 * math.ceil(max(item["max"] for item in stats.values()) / 5)
    if scale_max <= 0:
        scale_max = 45

    rows = math.ceil(len(parties) / COLS)
    width = PAGE_PAD * 2 + COLS * PANEL_W + (COLS - 1) * GAP
    height = HEADER + rows * PANEL_H + (rows - 1) * GAP + FOOTER + PAGE_PAD
    map_h = PANEL_H - 68
    tx, ty, _scale = map_transform(features, PANEL_W, map_h)

    defs_paths = []
    for item in features:
        number = str(item["cislo"])
        defs_paths.append(
            f'    <path id="p{number}" d="{path_d(item["rings"], tx, ty)}"/>'
        )
    casti = neighborhood_texts(neighborhoods or [], tx, ty, min_font=10, max_font=15)

    known = set(results["okrsky"])
    gradients = [legend_gradient("pctScale")]

    year = str(results.get("datum_voleb", ""))
    year_label = f"{year[6:8]}.{year[4:6]}.{year[:4]}" if len(year) == 8 else year
    heading = f"{nazev or results.get('obec') or 'MČ'} — podíl hlasů podle okrsků"

    panels = []
    for index, kod in enumerate(parties):
        col = index % COLS
        row = index // COLS
        x = PAGE_PAD + col * (PANEL_W + GAP)
        y = HEADER + row * (PANEL_H + GAP)
        info = stats[kod]
        rgb = party_rgb(kod)
        uses = []
        for item in features:
            number = str(item["cislo"])
            if number in known:
                pct = info["by_okrsek"].get(number, 0.0)
                fill = scale_color(pct / scale_max)
            else:
                fill = MISSING_FILL
            uses.append(
                f'      <use href="#p{number}" fill="{fill}" stroke="#334155" stroke-width="0.7"/>'
            )
        title = escape(info["short"])
        subtitle = (
            f'{fmt_pct(info["overall"])} celkem · okrsky {fmt_pct(info["min"])}–{fmt_pct(info["max"])}'
        )
        panels.append(
            f'''  <g transform="translate({x},{y})">
    <rect class="panel" width="{PANEL_W}" height="{PANEL_H}" rx="10"/>
    <circle cx="22" cy="24" r="7" fill="{hex_color(rgb)}"/>
    <text class="panel-title" x="36" y="24">{title}</text>
    <text class="panel-sub" x="36" y="44">{escape(subtitle)}</text>
    <g transform="translate(0,56)" fill-rule="evenodd">
{chr(10).join(uses)}
      <use href="#casti"/>
    </g>
  </g>'''
        )

    ticks = list(range(0, int(scale_max) + 1, 5 if scale_max <= 30 else 10))
    if ticks[-1] != int(scale_max):
        ticks.append(int(scale_max))
    legend_x = PAGE_PAD
    legend_y = height - FOOTER + 18
    legend_w = width - 2 * PAGE_PAD
    tick_svg = []
    for tick in ticks:
        txp = legend_x + legend_w * (tick / scale_max)
        tick_svg.append(
            f'<line x1="{txp:.1f}" y1="{legend_y + 18}" x2="{txp:.1f}" y2="{legend_y + 26}" stroke="#334155"/>'
        )
        tick_svg.append(
            f'<text class="legend-tick" x="{txp:.1f}" y="{legend_y + 42}">{tick} %</text>'
        )

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 {width:.1f} {height:.1f}" width="{width:.0f}" height="{height:.0f}">
  <title>{escape(f"{heading} ({year_label})")}</title>
  <defs>
    {"".join(gradients)}
{chr(10).join(defs_paths)}
    <g id="casti">
{chr(10).join(casti)}
    </g>
  </defs>
  <style>
    .panel {{ fill: #ffffff; stroke: #e2e8f0; }}
    .title {{
      font-family: "Source Sans 3", "Liberation Sans", "DejaVu Sans", sans-serif;
      font-size: 30px; font-weight: 700; fill: #0f172a;
      text-anchor: start; dominant-baseline: hanging;
    }}
    .subtitle {{
      font-family: "Source Sans 3", "Liberation Sans", "DejaVu Sans", sans-serif;
      font-size: 15px; fill: #475569;
      text-anchor: start; dominant-baseline: hanging;
    }}
    .panel-title {{
      font-family: "Source Sans 3", "Liberation Sans", "DejaVu Sans", sans-serif;
      font-size: 16px; font-weight: 700; fill: #0f172a;
      text-anchor: start; dominant-baseline: middle;
    }}
    .panel-sub {{
      font-family: "Source Sans 3", "Liberation Sans", "DejaVu Sans", sans-serif;
      font-size: 12px; fill: #475569;
      text-anchor: start; dominant-baseline: middle;
    }}
    .cast {{
      font-family: "Source Sans 3", "Liberation Sans", "DejaVu Sans", sans-serif;
      font-weight: 750;
      fill: #1e293b;
      fill-opacity: 0.8;
      stroke: #ffffff;
      stroke-width: 3.2;
      stroke-opacity: 0.92;
      paint-order: stroke fill;
      text-anchor: middle;
      dominant-baseline: middle;
      letter-spacing: 0.2px;
      pointer-events: none;
    }}
    .legend-label, .legend-tick {{
      font-family: "Source Sans 3", "Liberation Sans", "DejaVu Sans", sans-serif;
      fill: #334155;
    }}
    .legend-label {{ font-size: 14px; font-weight: 650; text-anchor: start; dominant-baseline: hanging; }}
    .legend-tick {{ font-size: 12px; text-anchor: middle; dominant-baseline: hanging; }}
  </style>
  <rect width="100%" height="100%" fill="#f8fafc"/>
  <text class="title" x="{PAGE_PAD}" y="20">{escape(heading)}</text>
  <text class="subtitle" x="{PAGE_PAD}" y="54">Komunální volby {year_label} · zastupitelstvo MČ · každá mapa = jedna kandidátní listina</text>
{chr(10).join(panels)}
  <text class="legend-label" x="{legend_x}" y="{legend_y - 6}">Legenda: podíl hlasů v okrsku</text>
  <rect x="{legend_x}" y="{legend_y + 18}" width="{legend_w}" height="16" fill="url(#pctScale)" stroke="#334155" stroke-width="0.6"/>
  {"".join(tick_svg)}
</svg>
"""


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Vygeneruje SVG mapy okrsků obarvené podle % hlasů stran."
    )
    add_uzemi_args(parser)
    parser.add_argument(
        "--year",
        choices=("2022", "2026"),
        default="2026",
    )
    parser.add_argument(
        "--diff",
        action="store_true",
        help="Mapa změny procentních bodů mezi KV 2022 a 2026. Bez --party všechny strany, které jsou v obou letech.",
    )
    parser.add_argument(
        "--party",
        help="Kód strany pro --diff. Bez zadání se kreslí všechny porovnatelné listiny.",
    )
    parser.add_argument("--geojson", type=Path)
    parser.add_argument("--katastry", type=Path)
    parser.add_argument("--results", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    uzemi = load_uzemi()
    cast = resolve_cast(args, uzemi)
    if cast is None:
        return 0
    geojson = args.geojson or cast.geojson
    katastry_path = args.katastry or cast.katastry
    features = collect_features(load_geojson(geojson, cast.kod_zastupitelstva))
    neighborhoods = neighborhood_labels(
        features,
        load_katastry(katastry_path, features, uzemi.obec),
        popisky=cast.popisky,
        posun=cast.posun_popisku,
    )

    if args.diff:
        before = load_results(cast.year_dir("2022") / "vysledky.json")
        after = load_results(args.results or (cast.year_dir("2026") / "vysledky.json"))
        before_codes = [row["kod"] for row in before["strany"]]
        after_codes = [row["kod"] for row in after["strany"]]
        comparable = [kod for kod in after_codes if kod in set(before_codes)]
        if args.party:
            codes = set(before_codes) | set(after_codes)
            if args.party not in codes:
                raise SystemExit(
                    f"Neznámý kód strany {args.party!r}. Dostupné: {', '.join(sorted(codes))}"
                )
            if args.party not in comparable:
                raise SystemExit(
                    f"Strana {args.party} není v obou letech. Porovnatelné: {', '.join(comparable)}"
                )
            parties = [args.party]
        else:
            parties = comparable
            skipped = [kod for kod in after_codes if kod not in set(before_codes)]
            if skipped:
                print(f"Přeskočeno (není v roce 2022): {', '.join(skipped)}")
        if not parties:
            raise SystemExit("Žádná strana není v obou letech.")
        for kod in parties:
            out_path = args.out or (cast.year_dir("2026") / f"okrsky-rozdil-{kod}.svg")
            if args.out and len(parties) > 1:
                raise SystemExit("--out lze použít jen s jednou stranou (--party).")
            svg = generate_diff_svg(features, before, after, kod, neighborhoods)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(svg, encoding="utf-8")
            print(f"SVG: {out_path} ({out_path.stat().st_size} B)")
        return 0

    results_path = args.results or (cast.year_dir(args.year) / "vysledky.json")
    out_path = args.out or (cast.year_dir(args.year) / "okrsky-vysledky.svg")
    results = load_results(results_path)
    svg = generate_svg(features, results, neighborhoods, cast.nazev)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(svg, encoding="utf-8")
    print(f"SVG: {out_path} ({out_path.stat().st_size} B)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
