# Agent

Pipeline pro okrsky Brno-střed. Lidský popis, příkazy a tvar JSON jsou v [README.md](README.md). Tady jsou omezení, která se při úpravách nesmí rozbít.

## Co repo je

Tři CLI skripty, žádný balíček, žádné testy, žádný web.

1. `load_streets.py` — PDF definice okrsků (volba prezidenta 2023) → ulice.
2. `download_results.py` — KV 2026 z volby.cz → hlasy po okrscích. Seznam okrsků bere z `output/ulice.csv`.
3. `generate_map_svg.py` — RÚIAN GeoJSON → `output/okrsky.svg`.

Konstanty území neměň, pokud o to uživatel výslovně nepožádá: datum `20261009`, kraj `6200`, okres `6202`, zastupitelstvo `550973`, okrsky `23001`–`23070` (přesně 70).

`output/` a `data/` jsou verzované. Ručně neupravuj desítky JSON v `output/vysledky/`. Po změně parseru nebo stahování znovu spusť příslušný skript.

## Kódy stran

Stabilní kód je `PARTY_CODE_OVERRIDES` v `download_results.py`, ne `slugify`. Novou listinu přidej do slovníku. Existující kód (`ODS_TOP09`, `PIRATI_FAKT`, …) nepřejmenovávej: je v `output/parties.json` a ve všech výsledcích.

```python
PARTY_CODE_OVERRIDES = {
    "ODS a TOP 09": "ODS_TOP09",
}
```

## Formáty, které drž

- Číslo okrsku ve výsledcích a v CSV ulic je řetězec (`"23001"`). V GeoJSON je `properties.cislo` celé číslo. Při spojení normalizuj, nespoléhej na shodu typů.
- `output/ulice.csv`: více okrsků v jedné buňce je `"23037; 23038"`. `download_results.py` to dělí regexem `[;,\s]+`.
- JSON se píše s `ensure_ascii=False`, odsazením 2 a koncovým novým řádkem. CSV je `utf-8-sig`.
- `prehled` ve zdroji volby.cz je poziční pole. `parse_prehled` záměrně přeskakuje indexy 3 a 4. Indexy neměň bez surového payloadu.
- Řádek `vysledky` je `[cislo_listiny, nazev, hlasy, procenta]`. `plne_nazvy_stran` je `[cislo, nazev_plny]`.
- SVG cesta má `class="okrsek"` a `data-okrsek` s číslem okrsku. `generate_map_svg.py` vyžaduje 70 polygonů a kreslí v EPSG:5514 (osa Y je převrácená).

## Parser ulic

Rozpoznání úseků visí na textu konkrétního PDF (`OKRSEK_SPLIT_RE`, `SPEC_RE`). Zjednodušení regulárního výrazu rozbije rozsahy jako `3-11 lichá` nebo `72 do konce sudá`. Po úpravě ověř `python3 load_streets.py --lookup "Barvičova" --cislo 11` a počty: 70 okrsků, stovky úseků, ne nula záznamů.

## Styl skriptů

Drž se okolního kódu: `argparse`, `pathlib`, typové anotace, české texty v CLI a v názvech dat (`okrsek`, `ulice`, `cislo_listiny`), anglické názvy funkcí tam, kde už jsou. Novou závislost přidej jen do `requirements.txt` a jen když nestačí standardní knihovna. `pypdf` je jen pro PDF.

Ověření změny: `--help`, jeden okrsek (`download_results.py --okrsek 23001`) nebo `--lookup`. Celé stažení 70 okrsků nespouštěj, pokud uživatel nechce obnovit data z volby.cz.
