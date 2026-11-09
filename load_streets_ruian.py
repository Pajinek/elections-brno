#!/usr/bin/env python3
"""Ulice podle volebních okrsků z adresních míst RÚIAN (ČÚZK).

Adresní místo v RÚIAN má `vo_kod` (volební okrsek) a `ulice`. Skript z toho
složí stejné soubory jako `load_streets.py` (`ulice_okrsky.csv`, `ulice.csv`,
`ulice.json`), takže funguje pro MČ bez PDF s definicí okrsků.

Číslo domu je orientační číslo, a když chybí, číslo popisné / evidenční.
Volební místnost RÚIAN nezná, sloupec `volebni_mistnost` zůstává prázdný.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
import urllib.request
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path

from load_streets import StreetRecord, aggregate_by_street, write_csv
from uzemi import Cast, Uzemi, add_uzemi_args, load_uzemi, resolve_cast

SERVER = "https://ags.cuzk.gov.cz/arcgis/rest/services/RUIAN/MapServer"
LAYER_ADRESY = 1
LAYER_ULICE = 4
PAGE = 2000
KOD_CHUNK = 40

# Adresa bez ulice: „Bohunice č.ev. 160, 62500 Brno“, „Bosonohy 815, 64200 Brno“
BEZ_ULICE_RE = re.compile(
    r"^(?P<cast>.+?)\s+(?P<ev>č\.ev\.\s*)?\d+[^,\s]*,\s*\d{3}\s?\d{2}\s"
)


def query(layer: int, where: str, fields: str, offset: int = 0) -> dict:
    url = f"{SERVER}/{layer}/query?" + urllib.parse.urlencode(
        {
            "where": where,
            "outFields": fields,
            "returnGeometry": "false",
            "orderByFields": "objectid",
            "resultOffset": offset,
            "resultRecordCount": PAGE,
            "f": "json",
        }
    )
    request = urllib.request.Request(url, headers={"User-Agent": "elections-brno/1.0"})
    with urllib.request.urlopen(request, timeout=120) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if "error" in payload:
        raise RuntimeError(f"RÚIAN: {payload['error']}")
    return payload


def query_all(layer: int, where: str, fields: str) -> list[dict]:
    rows: list[dict] = []
    offset = 0
    while True:
        payload = query(layer, where, fields, offset)
        batch = [item["attributes"] for item in payload.get("features", [])]
        rows.extend(batch)
        if not batch or not payload.get("exceededTransferLimit"):
            return rows
        offset += len(batch)


def load_okrsky(cast: Cast) -> dict[int, str]:
    """Kód volebního okrsku v RÚIAN → číslo okrsku (řetězec)."""
    if not cast.geojson.exists():
        raise SystemExit(
            f"Chybí {cast.geojson}. Spusť generate_map_svg.py --uzemi {cast.slug}."
        )
    data = json.loads(cast.geojson.read_text(encoding="utf-8"))
    return {
        int(item["properties"]["kod"]): str(int(item["properties"]["cislo"]))
        for item in data["features"]
    }


def load_ulice_nazvy(obec: int) -> dict[int, str]:
    rows = query_all(LAYER_ULICE, f"obec={obec}", "objectid,kod,nazev")
    return {int(row["kod"]): row["nazev"] for row in rows}


def load_adresy(kody: list[int]) -> list[dict]:
    fields = (
        "objectid,cislodomovni,cisloorientacni,cisloorientacnipismeno,"
        "ulice,vo_kod,adresa,nespravny"
    )
    rows: list[dict] = []
    for start in range(0, len(kody), KOD_CHUNK):
        chunk = ",".join(str(kod) for kod in kody[start : start + KOD_CHUNK])
        rows.extend(query_all(LAYER_ADRESY, f"vo_kod IN ({chunk})", fields))
    return [row for row in rows if not row.get("nespravny")]


def house_key(row: dict) -> tuple[int, str]:
    orientacni = row.get("cisloorientacni")
    if orientacni is not None:
        return int(orientacni), (row.get("cisloorientacnipismeno") or "").lower()
    return int(row.get("cislodomovni") or 0), ""


def street_name(row: dict, nazvy: dict[int, str]) -> tuple[str, bool]:
    """Název ulice a příznak, zda má smysl dělit čísla na lichá/sudá."""
    kod = row.get("ulice")
    if kod is not None and int(kod) in nazvy:
        return nazvy[int(kod)], True
    match = BEZ_ULICE_RE.match(row.get("adresa") or "")
    if match:
        cast = match.group("cast")
        return (f"{cast} (č.ev.)" if match.group("ev") else cast), False
    return "", True


def format_number(key: tuple[int, str]) -> str:
    return f"{key[0]}{key[1]}"


def describe_street(
    items: list[tuple[tuple[int, str], str]], parity: bool = True
) -> list[tuple[str, str]]:
    """Úseky ulice jako dvojice (popis, okrsek), ve stylu PDF (`13-29 lichá`)."""
    okrsky = {okrsek for _, okrsek in items}
    if len(okrsky) == 1:
        return [("celá", next(iter(okrsky)))]

    groups = (("lichá", 1), ("sudá", 0)) if parity else (("", -1),)
    result: list[tuple[str, str]] = []
    for strana, remainder in groups:
        sequence = sorted(
            (item for item in items if remainder < 0 or item[0][0] % 2 == remainder),
            key=lambda item: item[0],
        )
        runs: list[list[tuple[tuple[int, str], str]]] = []
        for item in sequence:
            if runs and runs[-1][-1][1] == item[1]:
                runs[-1].append(item)
            else:
                runs.append([item])
        for index, run in enumerate(runs):
            first, last = format_number(run[0][0]), format_number(run[-1][0])
            okrsek = run[0][1]
            if len(runs) == 1:
                popis = strana
            elif index == len(runs) - 1:
                popis = f"{first} do konce {strana}"
            elif first == last:
                popis = f"{first} {strana}"
            else:
                popis = f"{first}-{last} {strana}"
            result.append((popis.strip(), okrsek))
    return result


def build_records(adresy: list[dict], okrsky: dict[int, str], nazvy: dict[int, str]) -> list[StreetRecord]:
    by_street: dict[str, list[tuple[tuple[int, str], str]]] = defaultdict(list)
    parity: dict[str, bool] = {}
    for row in adresy:
        okrsek = okrsky.get(int(row["vo_kod"]))
        ulice, with_parity = street_name(row, nazvy)
        if okrsek is None or not ulice:
            continue
        by_street[ulice].append((house_key(row), okrsek))
        parity[ulice] = with_parity

    records: list[StreetRecord] = []
    for ulice in sorted(by_street, key=str.casefold):
        for popis, okrsek in describe_street(by_street[ulice], parity[ulice]):
            strana = "lichá" if "lichá" in popis else "sudá" if "sudá" in popis else ""
            records.append(
                StreetRecord(
                    ulice=ulice,
                    popis=popis,
                    strana=strana,
                    cislo_okrsku=okrsek,
                    volebni_mistnost="",
                )
            )
    records.sort(key=lambda row: (row.ulice.casefold(), int(row.cislo_okrsku)))
    return records


def process(cast: Cast, uzemi: Uzemi, out: Path, force: bool) -> int:
    target = out / "ulice.csv"
    if target.exists() and not force:
        print(f"{cast.slug}: {target} už existuje (přepsat: --force), přeskakuji.")
        return 0

    okrsky = load_okrsky(cast)
    nazvy = load_ulice_nazvy(uzemi.obec)
    adresy = load_adresy(sorted(okrsky))
    records = build_records(adresy, okrsky, nazvy)
    if not records:
        print(f"{cast.slug}: RÚIAN nevrátil žádné adresy s okrskem.", file=sys.stderr)
        return 2

    found = {row.cislo_okrsku for row in records}
    missing = sorted(set(okrsky.values()) - found, key=int)
    street_rows = aggregate_by_street(records)

    out.mkdir(parents=True, exist_ok=True)
    write_csv(
        out / "ulice_okrsky.csv",
        [asdict(row) for row in records],
        ["ulice", "popis", "strana", "cislo_okrsku", "volebni_mistnost"],
    )
    write_csv(target, street_rows, ["ulice", "cislo_okrsku", "pocet_okrsku", "rozdeleni"])
    (out / "ulice.json").write_text(
        json.dumps(street_rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print(
        f"{cast.slug}: okrsky {len(found)}/{len(okrsky)}, adresy {len(adresy)}, "
        f"úseky {len(records)}, ulice {len(street_rows)}"
    )
    if missing:
        print(f"{cast.slug}: okrsky bez adres v RÚIAN: {', '.join(missing)}", file=sys.stderr)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Načte ulice podle volebních okrsků z adresních míst RÚIAN."
    )
    add_uzemi_args(parser)
    parser.add_argument(
        "--all",
        action="store_true",
        help="Všechny městské části (jinak jen --uzemi nebo výchozí)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        help="Výstupní adresář (výchozí: output/<uzemi>/; jen bez --all)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Přepsat existující ulice.csv (např. z PDF)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    uzemi = load_uzemi()
    if args.all and args.out:
        print("--out nejde použít s --all.", file=sys.stderr)
        return 1
    if args.all:
        if args.list_uzemi:
            resolve_cast(args, uzemi)
            return 0
        casti = list(uzemi.casti.values())
    else:
        cast = resolve_cast(args, uzemi)
        if cast is None:
            return 0
        casti = [cast]

    status = 0
    for cast in casti:
        try:
            code = process(cast, uzemi, (args.out or cast.output_dir), args.force)
        except (OSError, RuntimeError) as error:
            print(f"{cast.slug}: {error}", file=sys.stderr)
            code = 3
        status = max(status, code)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
