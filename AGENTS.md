# Agent

Pipeline pro volební okrsky městských částí Brna. Lidský popis, příkazy a tvar JSON jsou v [README.md](README.md). Tady jsou omezení, která se při úpravách nesmí rozbít.

## Co repo je

CLI skripty, žádný balíček, žádné testy, žádný web. Území řídí `data/uzemi.json` a `uzemi.py`. Výchozí MČ je `brno-stred`.

1. `generate_map_svg.py` — RÚIAN GeoJSON → `output/<uzemi>/okrsky.svg`.
2. `load_streets.py` — PDF definice okrsků → ulice (`output/<uzemi>/`).
3. `download_results.py` — KV 2026 / 2022 → `output/<uzemi>/kv<rok>/`. Seznam okrsků bere z `ulice.csv`, jinak z GeoJSON.
4. `generate_results_map.py` — choropleth a mapa rozdílu 2022→2026.

Konstanty města (datum `20261009` / `20220923`, kraj `6200`, okres `6202`, obec `582786`) jsou v `data/uzemi.json`. Kódy MČ tam taky — neměň je bez explicitní žádosti. Výchozí Brno-střed má zastupitelstvo `550973` a okrsky `23001`–`23070` (přesně 70).

`output/` a `data/` jsou verzované. Ručně neupravuj desítky JSON v `output/<uzemi>/kv*/vysledky/`. Po změně parseru nebo stahování znovu spusť příslušný skript.

Hromadné generování je v `Makefile`: `make`, `make maps`, `make brno-stred`, `make UZEMI=brno-sever`. `make` / `make all` stahuje geometrii a výsledky všech 29 MČ z RÚIAN a volby.cz — nespouštěj to, pokud uživatel nechce obnovit nebo doplnit data. Jednotlivé `--okrsek` stačí na ověření parseru.

Novou MČ nedoplňuj ručním kopírováním JSON. Stačí slug v `data/uzemi.json` a `make <slug>`. GeoJSON se stáhne. Data jiných MČ nestahuj, dokud o to uživatel nepožádá.

## Kódy stran

Stabilní kód je `PARTY_CODE_OVERRIDES` v `download_results.py`, ne `slugify`. Novou listinu přidej do slovníku. Existující kód (`ODS_TOP09`, `PIRATI_FAKT`, …) nepřejmenovávej: je v `parties.json` a ve všech výsledcích.

```python
PARTY_CODE_OVERRIDES = {
    "ODS a TOP 09": "ODS_TOP09",
}
```

## Formáty, které drž

- Číslo okrsku ve výsledcích a v CSV ulic je řetězec (`"23001"`). V GeoJSON je `properties.cislo` celé číslo. Při spojení normalizuj, nespoléhej na shodu typů.
- `output/<uzemi>/ulice.csv`: více okrsků v jedné buňce je `"23037; 23038"`. `download_results.py` to dělí regexem `[;,\s]+`.
- JSON se píše s `ensure_ascii=False`, odsazením 2 a koncovým novým řádkem. CSV je `utf-8-sig`.
- `prehled` ve zdroji volby.cz je poziční pole. `parse_prehled` záměrně přeskakuje indexy 3 a 4. Indexy neměň bez surového payloadu.
- Řádek `vysledky` je `[cislo_listiny, nazev, hlasy, procenta]`. `plne_nazvy_stran` je `[cislo, nazev_plny]`.
- SVG cesta má `class="okrsek"` a `data-okrsek` s číslem okrsku. `generate_map_svg.py` kreslí v EPSG:5514 (osa Y je převrácená).

## Parser ulic

Rozpoznání úseků visí na textu konkrétního PDF (`OKRSEK_SPLIT_RE`, `SPEC_RE`). Zjednodušení regulárního výrazu rozbije rozsahy jako `3-11 lichá` nebo `72 do konce sudá`. Po úpravě ověř `python3 load_streets.py --lookup "Barvičova" --cislo 11` a počty: 70 okrsků, stovky úseků, ne nula záznamů.

## Styl skriptů

Drž se okolního kódu: `argparse`, `pathlib`, typové anotace, české texty v CLI a v názvech dat (`okrsek`, `ulice`, `cislo_listiny`), anglické názvy funkcí tam, kde už jsou. Novou závislost přidej jen do `requirements.txt` a jen když nestačí standardní knihovna. `pypdf` je jen pro PDF.

Ověření změny: `--help`, `--list-uzemi`, jeden okrsek (`download_results.py --okrsek 23001`) nebo `--lookup`. Cesty musí jít přes `uzemi.py`, ne hardcoded `output/vysledky.json`.
