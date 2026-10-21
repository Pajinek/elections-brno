#!/usr/bin/env python3
"""Download municipal-election results per Brno city district.

KV 2026 JSON API:
https://volby.gov.cz/appdata/kv2026/{datum}/vysled/okrsek/{okres}/{kodzastup}_{okrsek}.json

KV 2022 open data (ČSÚ CSV):
https://volby.gov.cz/opendata/kv2022/
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from uzemi import ROOT, Cast, Uzemi, add_uzemi_args, load_uzemi, resolve_cast

CACHE_DIR = ROOT / "cache" / "kv2022"

KV2022_FILES = {
    "kvt3.zip": "https://volby.gov.cz/opendata/kv2022/csv_od/kvt3_csvod.zip",
    "kvhl.zip": "https://volby.gov.cz/opendata/kv2022/csv_od/kvhl_csvod.zip",
    "kvros.csv": "https://volby.gov.cz/opendata/kv2022/csv_od/kvros.csv",
}


def page_url_2026(uzemi: Uzemi, cast: Cast, okrsek: str) -> str:
    datum = uzemi.datum_voleb["2026"]
    return (
        "https://volby.gov.cz/app/kv2026/cs/"
        f"{datum}/results/!___{uzemi.kraj}_{uzemi.okres}_{cast.kod_zastupitelstva}___{okrsek}"
    )


def api_url_2026(uzemi: Uzemi, cast: Cast, okrsek: str) -> str:
    datum = uzemi.datum_voleb["2026"]
    return (
        "https://volby.gov.cz/appdata/kv2026/"
        f"{datum}/vysled/okrsek/{uzemi.okres}/{cast.kod_zastupitelstva}_{okrsek}.json"
    )


def page_url_2022(uzemi: Uzemi, cast: Cast, okrsek: str) -> str:
    return (
        "https://volby.gov.cz/pls/kv2022/kv1111?xjazyk=CZ&xid=1&xdz=5"
        f"&xnumnuts={uzemi.okres}&xobec={cast.kod_zastupitelstva}&xstat=0&xvyber={okrsek}"
    )

USER_AGENT = "elections-brno/1.0 (+https://volby.gov.cz)"

# Stable placeholder codes for Brno-střed candidate lists (KV 2026).
PARTY_CODE_OVERRIDES = {
    "Brnoklidem": "BRNOKLIDEM",
    "Brno Brňankám a Brňanům! (B3!)": "B3",
    "ANO 2011": "ANO",
    "SPD a nezávislí": "SPD",
    "Lidovci a Starostové": "KDU_STAN",
    "Žít Zelené Brno (Zelení a Žít Brno)": "ZELENI_ZITBRNO",
    "Restart pro Brno": "RESTART",
    "ODS a TOP 09": "ODS_TOP09",
    "Piráti a Fakt Brno TU! pro Střed": "PIRATI_FAKT",
    "Brno-střed NAPLNO": "NAPLNO",
    "Za Lužánky": "ZALUZANKY",
    "Piráti, Zelení a Žít Brno": "PIRATI_ZELENI_ZITBRNO",
    "Zdravý střed s podporou Sociální demokracie": "ZDRAVY_STRED",
    "SPD, Trikolora, Moravané a nezávislí": "SPD",
    "Lidovci a Starostové (KDU-ČSL + Starostové a nezávislí)": "KDU_STAN",
    "RESTART PRO BRNO": "RESTART",
    "Fakt Brno": "FAKT",
    "SPOLEČNĚ - ODS a TOP 09": "ODS_TOP09",
}


def slugify(name: str) -> str:
    folded = unicodedata.normalize("NFKD", name)
    ascii_name = folded.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^A-Za-z0-9]+", "_", ascii_name).strip("_").upper()
    return slug or "STRANA"


def party_code(nazev: str) -> str:
    return PARTY_CODE_OVERRIDES.get(nazev) or slugify(nazev)


def parse_prehled(values: list[object]) -> dict[str, float | int]:
    padded = list(values) + [None] * 8
    return {
        "volici_v_seznamu": padded[0],
        "vydane_obalky": padded[1],
        "ucast_pct": padded[2],
        "odevzdane_obalky": padded[5],
        "odevzdane_pct": padded[6],
        "platne_hlasy": padded[7],
    }


def fetch_json(url: str, retries: int = 3) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            last_error = error
            time.sleep(attempt)
    raise RuntimeError(f"Nelze stáhnout {url}: {last_error}") from last_error


def load_okrsky(streets_csv: Path, geojson: Path | None = None) -> list[str]:
    if streets_csv.exists():
        numbers: set[str] = set()
        with streets_csv.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                raw = row.get("cislo_okrsku") or ""
                for part in re.split(r"[;,\s]+", raw):
                    if part.isdigit():
                        numbers.add(part)
        if numbers:
            return sorted(numbers, key=int)
    if geojson and geojson.exists():
        data = json.loads(geojson.read_text(encoding="utf-8"))
        numbers = {str(feature["properties"]["cislo"]) for feature in data["features"]}
        return sorted(numbers, key=int)
    return []


def build_party_map(payload: dict) -> dict[int, dict[str, str | int]]:
    mapping: dict[int, dict[str, str | int]] = {}
    full_names = {row[0]: row[1] for row in payload.get("plne_nazvy_stran") or []}
    for row in payload.get("vysledky") or []:
        cislo = int(row[0])
        nazev = str(row[1])
        mapping[cislo] = {
            "cislo_listiny": cislo,
            "kod": party_code(nazev),
            "nazev": nazev,
            "nazev_plny": full_names.get(cislo, nazev),
        }
    return mapping


def normalize_okrsek(
    payload: dict,
    parties: dict[int, dict[str, str | int]],
    uzemi: Uzemi,
    cast: Cast,
) -> dict:
    param = payload.get("param") or {}
    okrsek = str(param.get("okrsek"))
    strany = []
    for row in payload.get("vysledky") or []:
        cislo = int(row[0])
        info = parties[cislo]
        strany.append(
            {
                "cislo_listiny": cislo,
                "kod": info["kod"],
                "nazev": info["nazev"],
                "hlasy": row[2],
                "procenta": row[3],
            }
        )
    return {
        "okrsek": okrsek,
        "obec": payload.get("obecNazev") or cast.nazev,
        "kod_zastupitelstva": param.get("kodzastup") or cast.kod_zastupitelstva,
        "okres": param.get("okres") or uzemi.okres,
        "datum_voleb": param.get("datumvoleb"),
        "generovano": payload.get("generovano"),
        "url": page_url_2026(uzemi, cast, okrsek),
        "api": api_url_2026(uzemi, cast, okrsek),
        "prehled": parse_prehled(payload.get("prehled") or []),
        "strany": strany,
    }


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def csuz_pct(part: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return math.floor(part * 10000 / total) / 100


def fetch_bytes(url: str, dest: Path, retries: int = 3) -> Path:
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                dest.write_bytes(response.read())
            return dest
        except urllib.error.URLError as error:
            last_error = error
            time.sleep(attempt)
    raise RuntimeError(f"Nelze stáhnout {url}: {last_error}") from last_error


def iter_zip_csv(zip_path: Path, name: str):
    with zipfile.ZipFile(zip_path) as archive:
        with archive.open(name) as raw:
            with io.TextIOWrapper(raw, encoding="utf-8", newline="") as handle:
                yield from csv.DictReader(handle)


def load_kv2022_parties(
    kvros_path: Path, datum: str, kod_zastup: str
) -> dict[int, dict[str, str | int]]:
    mapping: dict[int, dict[str, str | int]] = {}
    with kvros_path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["DATUMVOLEB"] != datum or row["KODZASTUP"] != kod_zastup:
                continue
            cislo = int(row["POR_STR_HL"])
            nazev = row["NAZEVCELK"]
            mapping[cislo] = {
                "cislo_listiny": cislo,
                "kod": party_code(nazev),
                "nazev": nazev,
                "nazev_plny": nazev,
                "zkratka": row["ZKRATKAO8"],
            }
    return mapping


def load_kv2022_prehled(
    kvt3_zip: Path, datum: str, kod_zastup: str, typzastup: str
) -> dict[str, dict[str, int | float]]:
    prehled: dict[str, dict[str, int | float]] = {}
    for row in iter_zip_csv(kvt3_zip, "kvt3.csv"):
        if (
            row["DATUMVOLEB"] != datum
            or row["KODZASTUP"] != kod_zastup
            or row["TYPZASTUP"] != typzastup
        ):
            continue
        volici = int(row["VOL_SEZNAM"])
        vydane = int(row["VYD_OBALKY"])
        odevzdane = int(row["ODEVZ_OBAL"])
        platne = int(row["PL_HL_CELK"])
        prehled[row["OKRSEK"]] = {
            "volici_v_seznamu": volici,
            "vydane_obalky": vydane,
            "ucast_pct": csuz_pct(vydane, volici),
            "odevzdane_obalky": odevzdane,
            "odevzdane_pct": csuz_pct(odevzdane, vydane),
            "platne_hlasy": platne,
        }
    return prehled


def load_kv2022_hlasy(
    kvhl_zip: Path, datum: str, kod_zastup: str, typzastup: str
) -> dict[str, dict[int, int]]:
    hlasy: dict[str, dict[int, int]] = {}
    for row in iter_zip_csv(kvhl_zip, "kvhl.csv"):
        if (
            row["DATUMVOLEB"] != datum
            or row["OBEC"] != kod_zastup
            or row["TYPZASTUP"] != typzastup
        ):
            continue
        okrsek = row["OKRSEK"]
        hlasy.setdefault(okrsek, {})[int(row["POR_STR_HL"])] = int(row["POC_HLASU"])
    return hlasy


def download_kv2022(
    out_dir: Path,
    cache_dir: Path,
    okrsky: list[str],
    uzemi: Uzemi,
    cast: Cast,
) -> tuple[dict[str, dict], list[dict], list[str]]:
    kvt3_zip = fetch_bytes(KV2022_FILES["kvt3.zip"], cache_dir / "kvt3_csvod.zip")
    kvhl_zip = fetch_bytes(KV2022_FILES["kvhl.zip"], cache_dir / "kvhl_csvod.zip")
    kvros_csv = fetch_bytes(KV2022_FILES["kvros.csv"], cache_dir / "kvros.csv")
    datum = uzemi.datum_voleb["2022"]
    kod = str(cast.kod_zastupitelstva)
    typ = uzemi.typzastup_mc

    parties = load_kv2022_parties(kvros_csv, datum, kod)
    prehled = load_kv2022_prehled(kvt3_zip, datum, kod, typ)
    hlasy = load_kv2022_hlasy(kvhl_zip, datum, kod, typ)

    results: dict[str, dict] = {}
    failed: list[str] = []
    wanted = set(okrsky)
    available = sorted(wanted & set(prehled) & set(hlasy), key=int)
    for missing in sorted(wanted - set(available), key=int):
        failed.append(missing)

    for index, okrsek in enumerate(available, start=1):
        stats = prehled[okrsek]
        platne = int(stats["platne_hlasy"])
        strany = []
        for cislo in sorted(parties):
            info = parties[cislo]
            votes = hlasy[okrsek].get(cislo, 0)
            strany.append(
                {
                    "cislo_listiny": cislo,
                    "kod": info["kod"],
                    "nazev": info["nazev"],
                    "hlasy": votes,
                    "procenta": csuz_pct(votes, platne),
                }
            )
        record = {
            "okrsek": okrsek,
            "obec": cast.nazev,
            "kod_zastupitelstva": cast.kod_zastupitelstva,
            "okres": uzemi.okres,
            "datum_voleb": int(datum),
            "url": page_url_2022(uzemi, cast, okrsek),
            "zdroj": "https://volby.gov.cz/opendata/kv2022/",
            "prehled": stats,
            "strany": strany,
        }
        results[okrsek] = record
        write_json(out_dir / "vysledky" / f"{okrsek}.json", record)
        print(f"[{index}/{len(available)}] okrsek {okrsek}")
    return results, [parties[key] for key in sorted(parties)], failed


def write_outputs(
    out_dir: Path,
    results: dict[str, dict],
    party_rows: list[dict],
    uzemi: Uzemi,
    cast: Cast,
    datum_voleb: str,
) -> None:
    write_json(
        out_dir / "parties.json",
        {
            "zastupitelstvo": {
                "kod": cast.kod_zastupitelstva,
                "nazev": next(iter(results.values())).get("obec") or cast.nazev,
                "datum_voleb": datum_voleb,
                "okres": uzemi.okres,
            },
            "strany": party_rows,
            "podle_kodu": {row["kod"]: row for row in party_rows},
        },
    )
    write_json(
        out_dir / "vysledky.json",
        {
            "zastupitelstvo": cast.kod_zastupitelstva,
            "obec": next(iter(results.values())).get("obec") or cast.nazev,
            "datum_voleb": datum_voleb,
            "pocet_okrsku": len(results),
            "strany": party_rows,
            "okrsky": results,
        },
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Stáhne výsledky komunálních voleb za okrsky městské části a uloží je do JSON."
    )
    add_uzemi_args(parser)
    parser.add_argument(
        "--year",
        choices=("2022", "2026"),
        default="2026",
        help="Rok komunálních voleb",
    )
    parser.add_argument(
        "--out",
        type=Path,
        help="Výstupní adresář (výchozí: output/<uzemi>/kv<rok>/)",
    )
    parser.add_argument(
        "--streets",
        type=Path,
        help="CSV s čísly okrsků (výchozí: output/<uzemi>/ulice.csv)",
    )
    parser.add_argument(
        "--okrsek",
        action="append",
        help="Stáhnout jen zadané číslo okrsku (lze opakovat)",
    )
    return parser


def download_kv2026(
    out_dir: Path,
    okrsky: list[str],
    uzemi: Uzemi,
    cast: Cast,
) -> tuple[dict[str, dict], list[dict], list[str]]:
    parties: dict[int, dict[str, str | int]] = {}
    results: dict[str, dict] = {}
    failed: list[str] = []
    for index, okrsek in enumerate(okrsky, start=1):
        url = api_url_2026(uzemi, cast, okrsek)
        try:
            payload = fetch_json(url)
        except RuntimeError as error:
            print(error, file=sys.stderr)
            failed.append(okrsek)
            continue
        if not parties:
            parties = build_party_map(payload)
        record = normalize_okrsek(payload, parties, uzemi, cast)
        results[okrsek] = record
        write_json(out_dir / "vysledky" / f"{okrsek}.json", record)
        print(f"[{index}/{len(okrsky)}] okrsek {okrsek}")
    return results, [parties[key] for key in sorted(parties)], failed


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    uzemi = load_uzemi()
    cast = resolve_cast(args, uzemi)
    if cast is None:
        return 0
    streets = args.streets or cast.streets_csv
    okrsky = args.okrsek or load_okrsky(streets, cast.geojson)
    if not okrsky:
        print(
            "Nenalezeny žádné okrsky ke stažení. Nejdřív spusťte generate_map_svg.py "
            "nebo load_streets.py pro toto území.",
            file=sys.stderr,
        )
        return 1

    out_dir = args.out or cast.year_dir(args.year)
    if args.year == "2022":
        results, party_rows, failed = download_kv2022(
            out_dir, CACHE_DIR, okrsky, uzemi, cast
        )
    else:
        results, party_rows, failed = download_kv2026(out_dir, okrsky, uzemi, cast)

    if not results:
        print("Nepodařilo se stáhnout žádný okrsek.", file=sys.stderr)
        return 2

    write_outputs(out_dir, results, party_rows, uzemi, cast, uzemi.datum_voleb[args.year])
    print(f"Území: {cast.nazev} ({cast.slug})")
    print(f"Staženo okrsků: {len(results)}")
    print(f"Strany: {out_dir / 'parties.json'}")
    print(f"Výsledky: {out_dir / 'vysledky.json'}")
    if failed:
        print(f"Chyba u okrsků: {', '.join(failed)}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
