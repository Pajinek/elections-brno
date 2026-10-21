#!/usr/bin/env python3
"""Load streets from a presidential-election precinct PDF
and attach číslo okrsku to each street (including house-number ranges).

The source PDF lists streets per volební okrsek, so one street can appear
in several okrsky when house numbers are split.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from pypdf import PdfReader

from uzemi import add_uzemi_args, load_uzemi, resolve_cast

OKRSEK_SPLIT_RE = re.compile(r"ve volebním okrsku č\.\s*(\d+)\s*", re.IGNORECASE)
MISTNOST_RE = re.compile(
    r"je volební místnost v\s+(.*?)\s+pro voliče",
    re.IGNORECASE | re.DOTALL,
)
STREETS_RE = re.compile(r"pobytu:\s*(.*)", re.IGNORECASE | re.DOTALL)

HEADER_RE = re.compile(
    r"ÚŘAD MĚSTSKÉ ČÁSTI MĚSTA BRNA[^\n]*|"
    r"Dominikánská 2,\s*60169 Brno\s*VOLBY\s*01/2023|"
    r"VOLEBNÍ OKRSKY|"
    r"Volba prezidenta České republiky",
    re.IGNORECASE,
)

# House-number / odd-even specification that follows a street name.
SPEC_RE = re.compile(
    r"""
    (?:
        \s+
        (?:
            (?:lichá|sudá)
            |
            do\s+konce(?:\s+(?:lichá|sudá))?
            |
            č\.ev\.\s*[\d,]+
            |
            \d+[a-z]?
            (?:\s*,\s*\d+[a-z]?)*
            (?:\s*-\s*\d+[a-z]?)?
            (?:\s+do\s+konce)?
            (?:\s+(?:lichá|sudá))?
        )
    )+
    $
    """,
    re.VERBOSE | re.IGNORECASE,
)

NUMBER_RE = re.compile(r"^(\d+)([a-z]?)$", re.IGNORECASE)


@dataclass(frozen=True)
class StreetRecord:
    ulice: str
    popis: str
    strana: str
    cislo_okrsku: str
    volebni_mistnost: str


def normalize_text(text: str) -> str:
    text = text.replace("\x0c", " ")
    text = HEADER_RE.sub(" ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\s*\n\s*", " ", text)
    text = re.sub(r" {2,}", " ", text)
    text = re.sub(r"(\d)\s*-\s*(\d)", r"\1-\2", text)
    return text.strip()


def extract_pdf_text(pdf_path: Path) -> str:
    reader = PdfReader(str(pdf_path))
    pages = [(page.extract_text() or "") for page in reader.pages]
    return normalize_text("\n".join(pages))


def parse_street_item(item: str) -> tuple[str, str]:
    item = re.sub(r"\s+", " ", item).strip(" ;")
    if not item:
        return "", ""
    match = SPEC_RE.search(item)
    if not match:
        return item, "celá"
    ulice = item[: match.start()].strip()
    popis = item[match.start() :].strip()
    return ulice, popis or "celá"


def detect_strana(popis: str) -> str:
    lowered = popis.lower()
    if re.search(r"\blichá\b", lowered):
        return "lichá"
    if re.search(r"\bsudá\b", lowered):
        return "sudá"
    return ""


def parse_okrsky(text: str) -> list[StreetRecord]:
    parts = OKRSEK_SPLIT_RE.split(text)
    if len(parts) < 3:
        raise ValueError("V PDF se nenašly žádné volební okrsky.")

    records: list[StreetRecord] = []
    for index in range(1, len(parts), 2):
        cislo = parts[index]
        body = normalize_text(parts[index + 1])
        mistnost_match = MISTNOST_RE.search(body)
        mistnost = (
            re.sub(r"\s+", " ", mistnost_match.group(1)).strip(" ,")
            if mistnost_match
            else ""
        )
        streets_match = STREETS_RE.search(body)
        street_blob = streets_match.group(1).strip() if streets_match else ""
        items = [part.strip() for part in street_blob.split(";") if part.strip()]
        for item in items:
            ulice, popis = parse_street_item(item)
            if not ulice:
                continue
            records.append(
                StreetRecord(
                    ulice=ulice,
                    popis=popis,
                    strana=detect_strana(popis),
                    cislo_okrsku=cislo,
                    volebni_mistnost=mistnost,
                )
            )
    return records


def aggregate_by_street(records: list[StreetRecord]) -> list[dict[str, str]]:
    grouped: dict[str, dict[str, object]] = {}
    for record in records:
        bucket = grouped.setdefault(
            record.ulice,
            {
                "ulice": record.ulice,
                "okrsky": [],
                "popisy": [],
            },
        )
        okrsky: list[str] = bucket["okrsky"]  # type: ignore[assignment]
        popisy: list[str] = bucket["popisy"]  # type: ignore[assignment]
        if record.cislo_okrsku not in okrsky:
            okrsky.append(record.cislo_okrsku)
        detail = record.popis if record.popis != "celá" else "celá"
        popisy.append(f"{detail} → {record.cislo_okrsku}")
    rows = []
    for ulice in sorted(grouped, key=lambda name: name.casefold()):
        bucket = grouped[ulice]
        okrsky = bucket["okrsky"]  # type: ignore[assignment]
        rows.append(
            {
                "ulice": ulice,
                "cislo_okrsku": "; ".join(okrsky),
                "pocet_okrsku": str(len(okrsky)),
                "rozdeleni": "; ".join(bucket["popisy"]),  # type: ignore[arg-type]
            }
        )
    return rows


def house_int(value: str) -> int | None:
    match = NUMBER_RE.match(value.strip().lower())
    return int(match.group(1)) if match else None


def popis_matches(popis: str, cislo: str) -> bool:
    if not popis or popis == "celá":
        return True

    lowered = popis.lower()
    wanted = house_int(cislo)
    if wanted is None:
        return cislo.lower() in lowered.replace(" ", "")

    if "lichá" in lowered and wanted % 2 == 0:
        return False
    if "sudá" in lowered and wanted % 2 == 1:
        return False

    numbers = re.findall(r"\d+[a-z]?", lowered)
    if "č.ev." in lowered:
        return cislo.lower() in {item.lower() for item in numbers}

    if "do konce" in lowered:
        start = house_int(numbers[0]) if numbers else None
        return start is None or wanted >= start

    if re.search(r"\d+[a-z]?\s*-\s*\d+[a-z]?", lowered):
        start = house_int(numbers[0])
        end = house_int(numbers[1]) if len(numbers) > 1 else None
        if start is None:
            return True
        if end is None:
            return wanted >= start
        return start <= wanted <= end

    if numbers:
        wanted_token = cislo.strip().lower()
        tokens = [item.lower() for item in numbers]
        return wanted_token in tokens or str(wanted) in tokens

    return True


def normalize_name(name: str) -> str:
    return re.sub(r"\s+", " ", name).casefold().rstrip(".")


def lookup(records: list[StreetRecord], ulice: str, cislo: str | None = None) -> list[StreetRecord]:
    needle = normalize_name(ulice)
    matches = [row for row in records if normalize_name(row.ulice) == needle]
    if not matches:
        matches = [row for row in records if needle in normalize_name(row.ulice)]
    if cislo:
        matches = [row for row in matches if popis_matches(row.popis, cislo)]
    return matches


def write_csv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Načte ulice z PDF volebních okrsků a doplní číslo okrsku."
    )
    add_uzemi_args(parser)
    parser.add_argument(
        "--pdf",
        type=Path,
        help="Cesta k PDF s definicí volebních okrsků (výchozí: data/<uzemi>/definice-okrsku.pdf)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        help="Výstupní adresář pro CSV/JSON (výchozí: output/<uzemi>/)",
    )
    parser.add_argument("--lookup", metavar="ULICE", help="Vyhledat ulici")
    parser.add_argument("--cislo", help="Orientační číslo domu pro --lookup")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    uzemi = load_uzemi()
    cast = resolve_cast(args, uzemi)
    if cast is None:
        return 0
    pdf_path = (args.pdf or cast.pdf).expanduser().resolve()
    args.out = args.out or cast.output_dir
    if not pdf_path.exists():
        print(f"PDF neexistuje: {pdf_path}", file=sys.stderr)
        return 1

    records = parse_okrsky(extract_pdf_text(pdf_path))
    if args.lookup:
        found = lookup(records, args.lookup, args.cislo)
        if not found:
            print("Ulice (s daným číslem) nebyla nalezena.")
            return 2
        for row in found:
            print(
                f"{row.ulice} | {row.popis} | okrsek {row.cislo_okrsku} | {row.volebni_mistnost}"
            )
        return 0

    args.out.mkdir(parents=True, exist_ok=True)
    segment_rows = [asdict(row) for row in records]
    street_rows = aggregate_by_street(records)

    write_csv(
        args.out / "ulice_okrsky.csv",
        segment_rows,
        ["ulice", "popis", "strana", "cislo_okrsku", "volebni_mistnost"],
    )
    write_csv(
        args.out / "ulice.csv",
        street_rows,
        ["ulice", "cislo_okrsku", "pocet_okrsku", "rozdeleni"],
    )
    (args.out / "ulice.json").write_text(
        json.dumps(street_rows, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    okrsky = {row.cislo_okrsku for row in records}
    print(f"PDF: {pdf_path}")
    print(f"Okrsky: {len(okrsky)}")
    print(f"Úseky ulic: {len(records)}")
    print(f"Unikátní ulice: {len(street_rows)}")
    print(f"CSV po úsecích: {args.out / 'ulice_okrsky.csv'}")
    print(f"CSV po ulicích: {args.out / 'ulice.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
