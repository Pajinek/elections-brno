# Volby Brno

Datová pipeline pro městské části Brna: ulice podle volebních okrsků, výsledky komunálních voleb a SVG mapy.

Skripty berou `--uzemi <slug>`. Výchozí je `brno-stred`. Registr všech 29 MČ je v `data/uzemi.json`.

```bash
python3 download_results.py --list-uzemi
```

## Rozsah (výchozí `brno-stred`)

| Údaj | Hodnota |
| --- | --- |
| Volby | komunální (KV) 2026 (`20261009`) a 2022 (`20220923`) |
| Území | Brno-střed |
| Kód zastupitelstva (MOMC) | `550973` |
| Okres / kraj / obec | `6202` / `6200` / `582786` |
| Okrsky | 70, čísla `23001`–`23070` |

Komunální volby jsou ve skutečnosti dvoje: do zastupitelstva MČ a do zastupitelstva města Brna (magistrát, kód `582786`, `TYPZASTUP=1`). Okrsky jsou pro obě volby stejné, takže stahujeme obojí za okrsky dané MČ.

Ulice pocházejí z definice okrsků pro volbu prezidenta 2023. Geometrie okrsků je z RÚIAN (ČÚZK). Hlasy jsou z [volby.cz](https://volby.gov.cz).

## Struktura

Každá městská část má stejný tvar cest:

```
data/uzemi.json
data/<uzemi>/okrsky.geojson
data/<uzemi>/katastry.geojson
data/<uzemi>/definice-okrsku.pdf      # volitelné, ulice
output/<uzemi>/ulice.csv
output/<uzemi>/okrsky.svg
output/<uzemi>/kv2026/vysledky.json
output/<uzemi>/kv2022/vysledky.json
output/<uzemi>/kv2026-mesto/vysledky.json   # zastupitelstvo města Brna
output/<uzemi>/kv2022-mesto/vysledky.json
```

Novou MČ doplníte tak, že ji necháte v `data/uzemi.json` (už tam je) a spustíte skripty s `--uzemi brno-sever` (nebo jiným slugem). GeoJSON se stáhne sám; PDF s ulicemi je potřeba jen když chcete seznam ulic.

## Požadavky

Python 3.10 nebo novější. Jediná knihovna mimo standardní knihovnu je `pypdf` (parsování PDF s ulicemi).

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Postup

Všechny městské části (29 MČ, stahuje RÚIAN a volby.cz):

```bash
make
```

Jen jedna MČ, nebo jen mapy:

```bash
make brno-stred
make maps
make UZEMI=brno-sever
make png UZEMI=brno-stred
make help
```

Stejné kroky ručně:

```bash
python3 generate_map_svg.py --uzemi brno-stred
python3 load_streets.py --uzemi brno-stred
python3 download_results.py --uzemi brno-stred
python3 download_results.py --uzemi brno-stred --year 2022
python3 generate_results_map.py --uzemi brno-stred
python3 generate_results_map.py --uzemi brno-stred --diff
```

`download_results.py` bere seznam okrsků z `output/<uzemi>/ulice.csv`; když CSV chybí, použije čísla z GeoJSON.

### Ulice

`load_streets.py` čte `data/<uzemi>/definice-okrsku.pdf` a zapíše:

- `output/<uzemi>/ulice_okrsky.csv` — jeden řádek na úsek ulice
- `output/<uzemi>/ulice.csv` a `ulice.json` — ulice sloučené; `cislo_okrsku` je seznam oddělený `; `

```bash
python3 load_streets.py --lookup "Barvičova" --cislo 11
```

Pro MČ bez PDF složí stejné soubory `load_streets_ruian.py` z adresních míst RÚIAN (`vo_kod` = volební okrsek). Existující `ulice.csv` (z PDF) nepřepíše bez `--force`.

```bash
python3 load_streets_ruian.py --uzemi brno-sever
python3 load_streets_ruian.py --all
make streets-ruian
```

Číslo domu je orientační, a když chybí, popisné. Adresy bez ulice jsou pod názvem části obce, evidenční čísla pod `Část (č.ev.)`. Volební místnost RÚIAN nezná (`volebni_mistnost` je prázdná). Úseky (`13-29 lichá`, `31 do konce lichá`) se počítají z existujících adres, ne z textu vyhlášky.

### Výsledky

KV 2026 z

`https://volby.gov.cz/appdata/kv2026/{datum}/vysled/okrsek/{okres}/{kodzastup}_{okrsek}.json`

KV 2022 z otevřených CSV ČSÚ. Výstup je `output/<uzemi>/kv{rok}/`.

```bash
python3 download_results.py --okrsek 23001 --okrsek 23002
```

Zastupitelstvo města Brna (magistrát) za okrsky dané MČ. Stejné okrsky, jiný kód zastupitelstva (`582786`) a jiné kandidátky. Výstup je `output/<uzemi>/kv{rok}-mesto/`.

```bash
python3 download_results.py --zastupitelstvo mesto
python3 download_results.py --zastupitelstvo mesto --year 2022
python3 download_results.py --uzemi brno-sever --zastupitelstvo mesto --okrsek 23001
make mesto UZEMI=brno-stred
```

Návratové kódy: `0` v pořádku, `1` žádné okrsky, `2` nic se nestáhlo, `3` část okrsků selhala.

### Mapa

`generate_map_svg.py` stáhne polygony z RÚIAN (`momc=<kod zastupitelstva>`, EPSG:5514), když `data/<uzemi>/okrsky.geojson` chybí. Výsledek je `output/<uzemi>/okrsky.svg`.

`generate_results_map.py` kreslí choropleth podle % a s `--diff` mapu změny 2022→2026.

## Webová prezentace

```bash
cd web && python3 build.py          # data z kořene repozitáře → web/index.html
python3 build.py --src web/_src     # data z jiného checkoutu
```

Stránka má v hlavičce přepínač **Městské části / Magistrát města Brna**. Přepíná výsledky voleb do zastupitelstev MČ (`kv<rok>/`) a do zastupitelstva města (`kv<rok>-mesto/`) nad stejnými okrsky. Volba je v adrese: `?zastupitelstvo=mesto` (u MČ se vynechává). Pohled na celé Brno u magistrátu sčítá hlasy po jednotlivých listinách, u MČ je sloučí podle politické rodiny. Bez dat `kv*-mesto/` (`make mesto`) stránka magistrát nezobrazí.

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

Výsledek magistrátu má stejný tvar; liší se `obec` (`Brno`), `kod_zastupitelstva` (`582786`) a seznam stran. Mapy (`generate_results_map.py`) zatím čtou jen výsledky MČ.

`okrsek` ve výsledcích je řetězec. V GeoJSON je `properties.cislo` číslo. Při spojení obou zdrojů je sjednoťte.

Kódy stran jsou v `PARTY_CODE_OVERRIDES` v `download_results.py`. Neznámý název dostane kód ze `slugify`.

## Soubory

| Cesta | Role |
| --- | --- |
| `uzemi.py` | registr MČ a cesty |
| `data/uzemi.json` | kódy zastupitelstev všech 29 MČ |
| `load_streets.py` | PDF → CSV/JSON ulic |
| `download_results.py` | volby.cz → JSON výsledků |
| `generate_map_svg.py` | GeoJSON → SVG okrsků |
| `generate_results_map.py` | výsledky → choropleth / rozdíl |
