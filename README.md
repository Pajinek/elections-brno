# Volby Brno-střed

Datová pipeline pro městskou část Brno-střed: ulice podle volebních okrsků, výsledky komunálních voleb 2026 a SVG mapa okrsků.

Tři samostatné skripty v Pythonu. Výstupy v `output/` i zdrojová data v `data/` jsou součástí repozitáře.

## Rozsah

| Údaj | Hodnota |
| --- | --- |
| Volby | komunální (KV) 2026, datum `20261009` |
| Území | Brno-střed |
| Kód zastupitelstva (MOMC) | `550973` |
| Okres / kraj | `6202` / `6200` |
| Okrsky | 70, čísla `23001`–`23070` |
| Ulice | 286 názvů, 518 úseků (jedna ulice může spadat do více okrsků) |

Ulice pocházejí z definice okrsků pro volbu prezidenta 2023. Geometrie okrsků je z RÚIAN (ČÚZK). Hlasy jsou z otevřených dat [volby.cz](https://volby.gov.cz) pro KV 2026. Čísla okrsků se mezi těmito zdroji shodují.

## Požadavky

Python 3.10 nebo novější. Jediná knihovna mimo standardní knihovnu je `pypdf` (parsování PDF s ulicemi).

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Postup

Skripty jdou v tomto pořadí. `download_results.py` bere seznam okrsků z `output/ulice.csv`; když CSV chybí, stáhne `23001`–`23070`.

```bash
python3 load_streets.py
python3 download_results.py
python3 generate_map_svg.py
```

### Ulice

`load_streets.py` čte `data/volby_prezident_2023_definice_volebnich_okrsku.pdf` a zapíše:

- `output/ulice_okrsky.csv` — jeden řádek na úsek ulice (název, popis čísel, sudá/lichá, okrsek, volební místnost)
- `output/ulice.csv` a `output/ulice.json` — ulice sloučené; `cislo_okrsku` je seznam oddělený `; `

Vyhledání konkrétní adresy (bez zápisu souborů):

```bash
python3 load_streets.py --lookup "Barvičova" --cislo 11
```

Popis úseku v PDF má tvary jako `celá`, `3-11 lichá`, `72 do konce sudá` nebo `č.ev. 5`. `--cislo` je orientační číslo domu a filtruje úseky podle rozsahu, parity a výčtu.

### Výsledky

`download_results.py` stáhne JSON každého okrsku z

`https://volby.gov.cz/appdata/kv2026/20261009/vysled/okrsek/6202/550973_{okrsek}.json`

a uloží:

- `output/vysledky/{okrsek}.json` — normalizovaný záznam jednoho okrsku
- `output/vysledky.json` — všech 70 okrsků v jednom souboru
- `output/parties.json` — kandidátní listiny a stabilní kódy (`BRNOKLIDEM`, `ODS_TOP09`, …)

Jen vybrané okrsky:

```bash
python3 download_results.py --okrsek 23001 --okrsek 23002
```

Návratové kódy: `0` v pořádku, `1` žádné okrsky, `2` nic se nestáhlo, `3` část okrsků selhala (úspěšné se přesto zapíší).

### Mapa

`generate_map_svg.py` vezme `data/okrsky-brno-stred.geojson` (EPSG:5514, vrstva RÚIAN 20, filtr `momc=550973`). Když soubor chybí, stáhne ho z ArcGIS ČÚZK. Výsledek je `output/okrsky.svg`: jeden `<path class="okrsek" data-okrsek="23001">` na okrsek a popisek s číslem. Skript skončí chybou, když ve vstupu není přesně 70 polygonů.

## Tvar výsledku okrsku

```json
{
  "okrsek": "23001",
  "obec": "Brno-střed",
  "kod_zastupitelstva": 550973,
  "prehled": {
    "volici_v_seznamu": 16412,
    "vydane_obalky": 142,
    "ucast_pct": 0.87,
    "odevzdane_obalky": 134,
    "odevzdane_pct": 94.37,
    "platne_hlasy": 5892
  },
  "strany": [
    {"cislo_listiny": 1, "kod": "BRNOKLIDEM", "nazev": "Brnoklidem", "hlasy": 330, "procenta": 5.6}
  ]
}
```

`okrsek` ve výsledcích je řetězec. V GeoJSON je `properties.cislo` číslo. Při spojení obou zdrojů je sjednoťte (řetězec o pěti znacích, nebo `int`).

Kódy stran jsou v `PARTY_CODE_OVERRIDES` v `download_results.py`. Neznámý název dostane kód ze `slugify` (ASCII, velká písmena, podtržítka).

## Soubory

| Cesta | Role |
| --- | --- |
| `load_streets.py` | PDF → CSV/JSON ulic |
| `download_results.py` | volby.cz → JSON výsledků |
| `generate_map_svg.py` | GeoJSON → SVG |
| `data/volby_prezident_2023_definice_volebnich_okrsku.pdf` | definice okrsků a ulic |
| `data/okrsky-brno-stred.geojson` | polygony okrsků, EPSG:5514 |
| `output/` | vygenerovaná data a mapa, verzovaná v gitu |
