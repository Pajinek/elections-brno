#!/usr/bin/env python3
"""Download municipal-election 2026 results for each Brno-střed okrsek.

Source page:
https://volby.gov.cz/app/kv2026/cs/20261009/results/!___6200_6202_550973___23001

The SPA loads JSON from:
https://volby.gov.cz/appdata/kv2026/20261009/vysled/okrsek/6202/550973_23001.json
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path

DATUM_VOLEB = "20261009"
KRAJ = "6200"
OKRES = "6202"
KOD_ZASTUP = "550973"

PAGE_URL = (
    "https://volby.gov.cz/app/kv2026/cs/"
    f"{DATUM_VOLEB}/results/!___{KRAJ}_{OKRES}_{KOD_ZASTUP}___{{okrsek}}"
)
API_URL = (
    "https://volby.gov.cz/appdata/kv2026/"
    f"{DATUM_VOLEB}/vysled/okrsek/{OKRES}/{KOD_ZASTUP}_{{okrsek}}.json"
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


def load_okrsky(streets_csv: Path) -> list[str]:
    if streets_csv.exists():
        numbers: set[str] = set()
        with streets_csv.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                raw = row.get("cislo_okrsku") or ""
                for part in re.split(r"[;,\s]+", raw):
                    if part.isdigit():
                        numbers.add(part)
        if numbers:
            return sorted(numbers)
    return [f"{number:05d}" for number in range(23001, 23071)]


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


def normalize_okrsek(payload: dict, parties: dict[int, dict[str, str | int]]) -> dict:
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
        "obec": payload.get("obecNazev"),
        "kod_zastupitelstva": param.get("kodzastup"),
        "okres": param.get("okres"),
        "datum_voleb": param.get("datumvoleb"),
        "generovano": payload.get("generovano"),
        "url": PAGE_URL.format(okrsek=okrsek),
        "api": API_URL.format(okrsek=okrsek),
        "prehled": parse_prehled(payload.get("prehled") or []),
        "strany": strany,
    }


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Stáhne výsledky KV 2026 za okrsky Brno-střed a uloží je do JSON."
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).resolve().parent / "output",
        help="Výstupní adresář",
    )
    parser.add_argument(
        "--streets",
        type=Path,
        default=Path(__file__).resolve().parent / "output" / "ulice.csv",
        help="CSV s čísly okrsků (výstup load_streets.py)",
    )
    parser.add_argument(
        "--okrsek",
        action="append",
        help="Stáhnout jen zadané číslo okrsku (lze opakovat)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    okrsky = args.okrsek or load_okrsky(args.streets)
    if not okrsky:
        print("Nenalezeny žádné okrsky ke stažení.", file=sys.stderr)
        return 1

    parties: dict[int, dict[str, str | int]] = {}
    results: dict[str, dict] = {}
    failed: list[str] = []

    for index, okrsek in enumerate(okrsky, start=1):
        url = API_URL.format(okrsek=okrsek)
        try:
            payload = fetch_json(url)
        except RuntimeError as error:
            print(error, file=sys.stderr)
            failed.append(okrsek)
            continue
        if not parties:
            parties = build_party_map(payload)
        record = normalize_okrsek(payload, parties)
        results[okrsek] = record
        write_json(args.out / "vysledky" / f"{okrsek}.json", record)
        print(f"[{index}/{len(okrsky)}] okrsek {okrsek}")

    if not results:
        print("Nepodařilo se stáhnout žádný okrsek.", file=sys.stderr)
        return 2

    party_rows = [parties[key] for key in sorted(parties)]
    write_json(
        args.out / "parties.json",
        {
            "zastupitelstvo": {
                "kod": int(KOD_ZASTUP),
                "nazev": next(iter(results.values())).get("obec"),
                "datum_voleb": DATUM_VOLEB,
                "okres": int(OKRES),
            },
            "strany": party_rows,
            "podle_kodu": {row["kod"]: row for row in party_rows},
        },
    )
    write_json(
        args.out / "vysledky.json",
        {
            "zastupitelstvo": int(KOD_ZASTUP),
            "obec": next(iter(results.values())).get("obec"),
            "datum_voleb": DATUM_VOLEB,
            "pocet_okrsku": len(results),
            "strany": party_rows,
            "okrsky": results,
        },
    )

    print(f"Staženo okrsků: {len(results)}")
    print(f"Strany: {args.out / 'parties.json'}")
    print(f"Výsledky: {args.out / 'vysledky.json'}")
    if failed:
        print(f"Chyba u okrsků: {', '.join(failed)}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
