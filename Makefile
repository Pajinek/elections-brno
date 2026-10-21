# Pipeline pro všechny městské části z data/uzemi.json.
#   make              mapy, výsledky, choropleth, rozdíly (síť: RÚIAN + volby.cz)
#   make maps         jen GeoJSON + SVG okrsků
#   make UZEMI=brno-sever
#   make brno-stred   jedna MČ
#   make -j4 maps     paralelní stahování geometrie

PYTHON ?= python3
RSVG ?= rsvg-convert
PNG_ZOOM ?= 1
SHELL := /bin/bash

ifeq ($(origin UZEMI), command line)
else
UZEMI := $(shell $(PYTHON) -c "from uzemi import load_uzemi; print(' '.join(sorted(load_uzemi().casti)))")
endif

MAPS := $(addprefix output/,$(addsuffix /okrsky.svg,$(UZEMI)))
GEOJSON := $(addprefix data/,$(addsuffix /okrsky.geojson,$(UZEMI)))
KV2026 := $(addprefix output/,$(addsuffix /kv2026/vysledky.json,$(UZEMI)))
KV2022 := $(addprefix output/,$(addsuffix /kv2022/vysledky.json,$(UZEMI)))
CHORO2026 := $(addprefix output/,$(addsuffix /kv2026/okrsky-vysledky.svg,$(UZEMI)))
CHORO2022 := $(addprefix output/,$(addsuffix /kv2022/okrsky-vysledky.svg,$(UZEMI)))
DIFFS := $(addprefix output/,$(addsuffix /kv2026/.diff-stamp,$(UZEMI)))
STREETS := $(patsubst data/%/definice-okrsku.pdf,output/%/ulice.csv,$(wildcard data/*/definice-okrsku.pdf))
SVG_FOR_PNG := $(wildcard $(foreach u,$(UZEMI),output/$(u)/okrsky.svg output/$(u)/kv2022/*.svg output/$(u)/kv2026/*.svg))
PNGS := $(SVG_FOR_PNG:.svg=.png)

.DEFAULT_GOAL := all
.DELETE_ON_ERROR:
.SECONDARY:
.SUFFIXES:

.PHONY: all maps streets results choropleth diffs png help list $(UZEMI)

help:
	@echo "make [all]              GeoJSON, mapy, výsledky 2022/2026, choropleth, rozdíly"
	@echo "make maps               RÚIAN → okrsky.geojson + okrsky.svg (+ katastry)"
	@echo "make streets            PDF → ulice.csv (jen MČ s data/<uzemi>/definice-okrsku.pdf)"
	@echo "make results            stažení KV 2022 a 2026"
	@echo "make choropleth         SVG podílů hlasů"
	@echo "make diffs              SVG změny 2022→2026 (strany v obou letech)"
	@echo "make png                SVG → PNG (librsvg: rsvg-convert)"
	@echo "make UZEMI=brno-sever   jen jedna MČ"
	@echo "make brno-stred         totéž"
	@echo "make list               seznam MČ"
	@echo "PYTHON=$(PYTHON)  RSVG=$(RSVG)  PNG_ZOOM=$(PNG_ZOOM)"

list:
	@$(PYTHON) download_results.py --list-uzemi

all: maps streets results choropleth diffs

maps: $(MAPS)
streets: $(STREETS)
results: $(KV2026) $(KV2022)
choropleth: $(CHORO2026) $(CHORO2022)
diffs: $(DIFFS)
png: $(PNGS)

%.png: %.svg
	$(RSVG) -z $(PNG_ZOOM) -f png -o $@ $<

$(UZEMI):
	$(MAKE) UZEMI=$@ all

output/%/okrsky.svg data/%/okrsky.geojson data/%/katastry.geojson &:
	$(PYTHON) generate_map_svg.py --uzemi $*

output/%/ulice.csv output/%/ulice.json output/%/ulice_okrsky.csv &: data/%/definice-okrsku.pdf
	$(PYTHON) load_streets.py --uzemi $*

output/%/kv2026/vysledky.json output/%/kv2026/parties.json &: | data/%/okrsky.geojson
	$(PYTHON) download_results.py --uzemi $* --year 2026

output/%/kv2022/vysledky.json output/%/kv2022/parties.json &: | data/%/okrsky.geojson
	$(PYTHON) download_results.py --uzemi $* --year 2022

output/%/kv2026/okrsky-vysledky.svg: output/%/kv2026/vysledky.json data/%/okrsky.geojson
	$(PYTHON) generate_results_map.py --uzemi $* --year 2026

output/%/kv2022/okrsky-vysledky.svg: output/%/kv2022/vysledky.json data/%/okrsky.geojson
	$(PYTHON) generate_results_map.py --uzemi $* --year 2022

output/%/kv2026/.diff-stamp: output/%/kv2026/vysledky.json output/%/kv2022/vysledky.json data/%/okrsky.geojson
	$(PYTHON) generate_results_map.py --uzemi $* --diff
	@touch $@
