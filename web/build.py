"""Sestaví index.html z dat repozitáře elections-brno (složka _src).

python build.py  →  index.html (jeden soubor, data jsou vložená)
"""
import csv
import json
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).parent
SRC = ROOT / "_src"
YEARS = ("2026", "2022")
TOL = 2.5  # tolerance zjednodušení hranic v metrech

FAMILIES = [
    ("ANO", r"(^|_)ANO(_|$)"),
    ("SPD", r"(^|_)SPD(_|$)"),
    ("RESTART", r"RESTART"),
    ("BRNOKLIDEM", r"BRNOKLIDEM"),
    ("MOTORISTE", r"MOTORIST"),
    ("NAPLNO", r"NAPLNO"),
    ("ODS", r"(^|_)ODS(_|$)|OBCANSKA_DEMOKRATICKA|(^|_)SPOLU(_|$)"),
    ("CSSD", r"CSSD|SOCIALN"),
    ("KDU", r"KDU|LIDOVC|STAROSTOVE|STAROSTU|(^|_)STAN(_|$)"),
    ("TOP09", r"TOP_?09"),
    ("PIRATI", r"PIRAT|(^|_)FAKT(_|$)"),
    ("ZELENI", r"ZELEN|ZIT"),
]


def family(kod):
    for name, rx in FAMILIES:
        if re.search(rx, kod):
            return name
    return "LOCAL"


def load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def dp(pts, tol):
    """Douglas–Peucker, iterativně."""
    if len(pts) < 3:
        return pts
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        ax, ay = pts[a]
        bx, by = pts[b]
        dx, dy = bx - ax, by - ay
        L = (dx * dx + dy * dy) ** 0.5
        best, bi = -1.0, -1
        for i in range(a + 1, b):
            px, py = pts[i]
            if L == 0:
                d = ((px - ax) ** 2 + (py - ay) ** 2) ** 0.5
            else:
                d = abs(dy * px - dx * py + bx * ay - by * ax) / L
            if d > best:
                best, bi = d, i
        if best > tol:
            keep[bi] = True
            stack += [(a, bi), (bi, b)]
    return [p for p, k in zip(pts, keep) if k]


def rings_of(geom):
    if geom["type"] == "Polygon":
        return list(geom["coordinates"])
    return [r for poly in geom["coordinates"] for r in poly]


def path_d(rings, proj, closed=True):
    out = []
    for ring in rings:
        pts = [proj(x, y) for x, y in ring]
        if closed and len(pts) > 3 and pts[0] == pts[-1]:
            # u uzavřeného kruhu drž dva kotevní body, ať DP nezkolabuje
            mid = len(pts) // 2
            s = dp(pts[: mid + 1], TOL)[:-1] + dp(pts[mid:], TOL)
        else:
            s = dp(pts, TOL)
        s = [(round(x), round(y)) for x, y in s]
        dedup = [s[0]]
        for p in s[1:]:
            if p != dedup[-1]:
                dedup.append(p)
        if len(dedup) < (3 if closed else 2):
            continue
        seg = [f"M{dedup[0][0]} {dedup[0][1]}"]
        px, py = dedup[0]
        rel = []
        for x, y in dedup[1:]:
            rel.append(f"{x - px} {y - py}")
            px, py = x, y
        seg.append("l" + " ".join(rel))
        out.append("".join(seg) + ("z" if closed else ""))
    return "".join(out)


def outline(rings_list):
    """Vnější hranice MČ = hrany, které patří jen jednomu okrsku."""
    cnt = defaultdict(int)
    for rings in rings_list:
        for ring in rings:
            for a, b in zip(ring, ring[1:]):
                ka = (round(a[0], 1), round(a[1], 1))
                kb = (round(b[0], 1), round(b[1], 1))
                cnt[(ka, kb) if ka < kb else (kb, ka)] += 1
    edges = [e for e, n in cnt.items() if n == 1]
    adj = defaultdict(list)
    for a, b in edges:
        adj[a].append(b)
        adj[b].append(a)
    used = set()
    lines = []
    for a, b in edges:
        key = (a, b)
        if key in used:
            continue
        used.add(key)
        line = [a, b]
        for forward in (True, False):
            while True:
                end = line[-1] if forward else line[0]
                nxt = None
                for c in adj[end]:
                    k = (end, c) if end < c else (c, end)
                    if k not in used:
                        nxt, used_k = c, k
                        break
                if nxt is None:
                    break
                used.add(used_k)
                if forward:
                    line.append(nxt)
                else:
                    line.insert(0, nxt)
        lines.append(line)
    return lines


def main():
    uz = load(SRC / "data/uzemi.json")
    casti = uz["casti"]

    feats = {}
    minx = miny = float("inf")
    maxx = maxy = float("-inf")
    for slug in casti:
        g = load(SRC / f"data/{slug}/okrsky.geojson")
        feats[slug] = g["features"]
        for f in g["features"]:
            for ring in rings_of(f["geometry"]):
                for x, y in ring:
                    minx, maxx = min(minx, x), max(maxx, x)
                    miny, maxy = min(miny, y), max(maxy, y)

    # EPSG:5514: x roste na východ, y (záporné) roste na sever → SVG y = maxy - y
    proj = lambda x, y: (x - minx, maxy - y)

    out = {"w": round(maxx - minx), "h": round(maxy - miny), "mc": []}

    for slug, meta in casti.items():
        mc = {"slug": slug, "name": meta["nazev"], "kod": meta["kod_zastupitelstva"],
              "parties": {}, "ok": []}
        results = {}
        for y in YEARS:
            p = SRC / f"output/{slug}/kv{y}/vysledky.json"
            if not p.exists():
                continue
            d = load(p)
            first = next(iter(d["okrsky"].values()), None)
            if first and first.get("url"):
                mc.setdefault("url", {})[y] = first["url"].replace(first["okrsek"], "{c}")
            mc["parties"][y] = [
                {"k": s["kod"], "n": s["nazev"], "c": s["cislo_listiny"], "f": family(s["kod"])}
                for s in d["strany"]
            ]
            results[y] = (d["strany"], d["okrsky"])

        all_rings = []
        for f in sorted(feats[slug], key=lambda f: f["properties"]["cislo"]):
            cislo = str(f["properties"]["cislo"])
            rings = rings_of(f["geometry"])
            all_rings.append(rings)
            xs = [proj(x, y) for r in rings for x, y in r]
            ok = {"c": cislo, "d": path_d(rings, proj),
                  "b": [round(min(p[0] for p in xs)), round(min(p[1] for p in xs)),
                        round(max(p[0] for p in xs)), round(max(p[1] for p in xs))],
                  "r": {}}
            for y, (strany, okrsky) in results.items():
                o = okrsky.get(cislo)
                if not o:
                    continue
                pr = o["prehled"]
                by = {s["kod"]: s["hlasy"] for s in o["strany"]}
                ok["r"][y] = [pr["volici_v_seznamu"], pr["vydane_obalky"], pr["odevzdane_obalky"],
                              pr["platne_hlasy"], [by.get(s["kod"], 0) for s in strany]]
            mc["ok"].append(ok)
        mc["outline"] = path_d(outline(all_rings), proj, closed=False)
        out["mc"].append(mc)

    # ulice (zatím jen Brno-střed)
    streets = {}
    for slug in casti:
        p = SRC / f"output/{slug}/ulice_okrsky.csv"
        if not p.exists():
            continue
        rows = []
        with open(p, encoding="utf-8-sig", newline="") as fh:
            for r in csv.DictReader(fh):
                rows.append([r["ulice"], r["popis"], r["strana"], r["cislo_okrsku"], r["volebni_mistnost"]])
        streets[slug] = rows
    out["streets"] = streets

    data = json.dumps(out, ensure_ascii=False, separators=(",", ":"))
    tpl = (ROOT / "template.html").read_text(encoding="utf-8")
    html = tpl.replace("/*__DATA__*/null", data)
    # artifact.html bez kostry (doplní ji claude.ai), index.html pro otevření ze souboru
    (ROOT / "artifact.html").write_text(html, encoding="utf-8")
    page = ("<!doctype html>\n<html lang=\"cs\">\n<head>\n<meta charset=\"utf-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1, viewport-fit=cover\">\n"
            "<style>body{margin:0}[hidden]{display:none!important}</style>\n</head>\n<body>\n"
            + html + "\n</body>\n</html>\n")
    (ROOT / "index.html").write_text(page, encoding="utf-8")
    print(f"index.html: {len(page.encode()) / 1024:.0f} kB, data {len(data.encode()) / 1024:.0f} kB")


if __name__ == "__main__":
    main()
