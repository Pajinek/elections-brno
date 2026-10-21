"""Registry of Brno municipal districts (městské části) and data paths."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent
UZEMI_PATH = ROOT / "data" / "uzemi.json"


@dataclass(frozen=True)
class Cast:
    slug: str
    nazev: str
    kod_zastupitelstva: int
    popisky: dict[str, str]
    posun_popisku: dict[str, tuple[float, float]]

    @property
    def data_dir(self) -> Path:
        return ROOT / "data" / self.slug

    @property
    def output_dir(self) -> Path:
        return ROOT / "output" / self.slug

    @property
    def geojson(self) -> Path:
        return self.data_dir / "okrsky.geojson"

    @property
    def katastry(self) -> Path:
        return self.data_dir / "katastry.geojson"

    @property
    def pdf(self) -> Path:
        return self.data_dir / "definice-okrsku.pdf"

    @property
    def streets_csv(self) -> Path:
        return self.output_dir / "ulice.csv"

    def year_dir(self, year: str | int) -> Path:
        return self.output_dir / f"kv{year}"


@dataclass(frozen=True)
class Uzemi:
    kraj: int
    okres: int
    obec: int
    typzastup_mc: str
    datum_voleb: dict[str, str]
    vychozi: str
    casti: dict[str, Cast]

    def cast(self, slug: str | None = None) -> Cast:
        key = slug or self.vychozi
        if key not in self.casti:
            available = ", ".join(sorted(self.casti))
            raise SystemExit(f"Neznámé území {key!r}. Dostupné: {available}")
        return self.casti[key]


def load_uzemi(path: Path = UZEMI_PATH) -> Uzemi:
    raw = json.loads(path.read_text(encoding="utf-8"))
    casti = {}
    for slug, row in raw["casti"].items():
        nudges = {}
        for nazev, xy in (row.get("posun_popisku") or {}).items():
            nudges[nazev] = (float(xy[0]), float(xy[1]))
        casti[slug] = Cast(
            slug=slug,
            nazev=row["nazev"],
            kod_zastupitelstva=int(row["kod_zastupitelstva"]),
            popisky=dict(row.get("popisky") or {}),
            posun_popisku=nudges,
        )
    return Uzemi(
        kraj=int(raw["kraj"]),
        okres=int(raw["okres"]),
        obec=int(raw["obec"]),
        typzastup_mc=str(raw["typzastup_mc"]),
        datum_voleb={str(year): str(datum) for year, datum in raw["datum_voleb"].items()},
        vychozi=str(raw["vychozi"]),
        casti=casti,
    )


def add_uzemi_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--uzemi",
        metavar="SLUG",
        help="Městská část (výchozí: brno-stred). Viz --list-uzemi.",
    )
    parser.add_argument(
        "--list-uzemi",
        action="store_true",
        help="Vypsat městské části a skončit",
    )


def resolve_cast(args: argparse.Namespace, uzemi: Uzemi | None = None) -> Cast | None:
    config = uzemi or load_uzemi()
    if args.list_uzemi:
        for slug, cast in config.casti.items():
            print(f"{slug}\t{cast.kod_zastupitelstva}\t{cast.nazev}")
        return None
    return config.cast(args.uzemi)
