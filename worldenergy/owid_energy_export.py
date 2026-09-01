"""
owid_energy_export.py
=====================
Downloads Our World in Data's consolidated energy dataset
(``owid-energy-data.csv`` -- ~9 MB, one row per entity-year, 130 columns) plus
its codebook, filters it down to a curated set of entities / years / columns,
reshapes it into a compact per-entity columnar structure, and writes
``worldenergy/energy_data.json`` for use by ``worldenergy/index.html``
(madadim.net/worldenergy).

Why pre-fetch instead of loading the CSV in the browser: same reason as every
other non-CBS source on this site (see the repo README). The raw file is ~9 MB
of mostly-empty cells; the browser only needs ~65 of the 130 columns, only a
handful of the 300+ "entities" (most of which are source-internal regions like
"Africa (EI)"), and it needs them keyed by entity rather than as a flat table.

Licence: OWID publishes this dataset for reuse -- the energy-data repo's own
README states the data is "made available under the Creative Commons BY licence"
and is "free to use, modify, and distribute ... for any purpose". It is rebuilt
by OWID from the Energy Institute Statistical Review of World Energy, Ember,
the U.S. EIA, and OWID's own population / GDP series.

Run any time you want fresh data:
    python owid_energy_export.py

Override the output path:
    python owid_energy_export.py --out /path/to/energy_data.json

Requirements:
    pip install requests
"""

import argparse
import csv
import io
import json
import os
import sys
from datetime import datetime

import requests

# -- Config -------------------------------------------------------------------

CSV_URL = "https://raw.githubusercontent.com/owid/energy-data/master/owid-energy-data.csv"
CODEBOOK_URL = "https://raw.githubusercontent.com/owid/energy-data/master/owid-energy-codebook.csv"

# The detailed fuel breakdown and carbon-intensity series only begin in the
# mid-1960s (Energy Institute) / 2000 (Ember). Pre-1965 rows are almost all
# fossil-fuel production tonnage only, which this page doesn't chart.
YEAR_MIN = 1965

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}

# Aggregate entities to keep: OWID source-name -> display name.
#
# OWID's CSV carries ~90 entities with no ISO code. Most are source-internal
# groupings ("Europe (EI)", "OPEC (EIA)", "Non-OECD (Shift)", ...) that overlap
# and double-count each other. We keep only:
#   * OWID's own canonical aggregates (World, continents, income groups, EU-27),
#     which carry the full ~130-column breakdown and are internally consistent;
#   * a few widely-cited blocs, each taken from ONE source lineage so a single
#     entity is never a mix of methodologies:
#       OECD -> "(EI)"     energy + electricity mix, 1965+, no carbon-intensity
#       G7   -> "(Ember)"  electricity side only, 2000+
#       G20  -> "(Ember)"  electricity side only, 2000+
#       OPEC -> "(EI)"     oil production only (that is all EI reports for OPEC)
# Anything not in this map and lacking an ISO code is dropped.
AGGREGATES = {
    "World": "World",
    "Africa": "Africa",
    "Asia": "Asia",
    "Europe": "Europe",
    "North America": "North America",
    "South America": "South America",
    "Oceania": "Oceania",
    "European Union (27)": "European Union",
    "High-income countries": "High-income countries",
    "Upper-middle-income countries": "Upper-middle-income countries",
    "Lower-middle-income countries": "Lower-middle-income countries",
    "Low-income countries": "Low-income countries",
    "OECD (EI)": "OECD",
    "G7 (Ember)": "G7",
    "G20 (Ember)": "G20",
    "OPEC (EI)": "OPEC",
}

# The nine fuel/source slices. For any given entity+year:
#   sum(twh_elec  for all 9) == electricity_generation
#   sum(twh_energy for all 9) ~= primary_energy_consumption  (substitution method;
#       small residual because OWID's "primary" total has minor extra categories)
# "other" uses the biofuel-EXCLUDING electricity column so bioenergy isn't
# counted twice in the electricity stack.
FUELS = [
    # key,      label,              color,     twh_energy,                    share_energy,                     twh_elec,                                   share_elec
    ("coal",    "Coal",             "#6b7280", "coal_consumption",            "coal_share_energy",              "coal_electricity",                         "coal_share_elec"),
    ("oil",     "Oil",              "#92400e", "oil_consumption",             "oil_share_energy",               "oil_electricity",                          "oil_share_elec"),
    ("gas",     "Gas",              "#f59e0b", "gas_consumption",             "gas_share_energy",               "gas_electricity",                          "gas_share_elec"),
    ("nuclear", "Nuclear",          "#c084fc", "nuclear_consumption",         "nuclear_share_energy",           "nuclear_electricity",                      "nuclear_share_elec"),
    ("hydro",   "Hydro",            "#3b82f6", "hydro_consumption",           "hydro_share_energy",             "hydro_electricity",                        "hydro_share_elec"),
    ("wind",    "Wind",             "#22d3ee", "wind_consumption",            "wind_share_energy",              "wind_electricity",                         "wind_share_elec"),
    ("solar",   "Solar",            "#fde047", "solar_consumption",           "solar_share_energy",             "solar_electricity",                        "solar_share_elec"),
    ("biofuel", "Bioenergy",        "#84cc16", "biofuel_consumption",         "biofuel_share_energy",           "biofuel_electricity",                      "biofuel_share_elec"),
    ("other",   "Other renewables", "#2dd4bf", "other_renewable_consumption", "other_renewables_share_energy",  "other_renewable_exc_biofuel_electricity",  "other_renewables_share_elec_exc_biofuel"),
]

# Non-fuel columns to carry through (headline totals, intensity, trade, rollups,
# production, context). Fuel columns are added programmatically from FUELS.
BASE_COLS = [
    # context
    "population", "gdp",
    # energy totals
    "primary_energy_consumption", "energy_per_capita", "energy_per_gdp",
    "energy_cons_change_pct", "electricity_share_energy",
    # electricity totals
    "electricity_generation", "electricity_demand", "per_capita_electricity",
    "electricity_demand_per_capita", "net_elec_imports", "net_elec_imports_share_demand",
    # carbon
    "carbon_intensity_elec", "greenhouse_gas_emissions",
    # fossil / low-carbon / renewable rollups
    "fossil_fuel_consumption", "fossil_share_energy", "fossil_electricity", "fossil_share_elec",
    "low_carbon_consumption", "low_carbon_share_energy", "low_carbon_electricity", "low_carbon_share_elec",
    "renewables_consumption", "renewables_share_energy", "renewables_electricity", "renewables_share_elec",
    # fossil-fuel production
    "coal_production", "gas_production", "oil_production",
]

FUEL_COLS = [c for f in FUELS for c in (f[3], f[4], f[5], f[6])]
KEEP_COLS = BASE_COLS + FUEL_COLS

# Compare-mode dropdown, grouped. Every entry must be in KEEP_COLS.
METRIC_GROUPS = [
    ("Energy — totals", [
        "primary_energy_consumption", "energy_per_capita", "energy_per_gdp",
        "energy_cons_change_pct", "electricity_share_energy",
    ]),
    ("Electricity — totals", [
        "electricity_generation", "electricity_demand", "per_capita_electricity",
        "electricity_demand_per_capita", "net_elec_imports", "net_elec_imports_share_demand",
    ]),
    ("Carbon", ["carbon_intensity_elec", "greenhouse_gas_emissions"]),
    ("Fossil / low-carbon / renewable — share of electricity (%)", [
        "fossil_share_elec", "low_carbon_share_elec", "renewables_share_elec",
    ]),
    ("Fossil / low-carbon / renewable — share of primary energy (%)", [
        "fossil_share_energy", "low_carbon_share_energy", "renewables_share_energy",
    ]),
    ("Fossil / low-carbon / renewable — absolute (TWh)", [
        "fossil_electricity", "low_carbon_electricity", "renewables_electricity",
        "fossil_fuel_consumption", "low_carbon_consumption", "renewables_consumption",
    ]),
    ("By source — share of electricity (%)", [f[6] for f in FUELS]),
    ("By source — share of primary energy (%)", [f[4] for f in FUELS]),
    ("By source — electricity generation (TWh)", [f[5] for f in FUELS]),
    ("By source — primary energy (TWh)", [f[3] for f in FUELS]),
    ("Fossil-fuel production (TWh)", ["coal_production", "gas_production", "oil_production"]),
    ("Context", ["population", "gdp"]),
]

INT_COLS = {"population", "gdp"}

# Compact unit tokens keyed off the codebook's verbose unit strings.
UNIT_MAP = {
    "terawatt-hours (twh)": "TWh",
    "kilowatt-hours per person (kwh)": "kWh/person",
    "kilowatt-hours per $ (kwh)": "kWh/$",
    "kilowatt-hours (kwh)": "kWh/person",
    "grams of co₂ equivalents per kilowatt-hour (gco₂)": "gCO₂/kWh",
    "million tonnes co₂ equivalents (mt)": "Mt CO₂e",
    "%": "%",
    "people": "people",
    "international-$ in 2011 prices ($)": "int-$ (2011)",
}


# -- Fetch ------------------------------------------------------------------

def fetch_csv(session, url):
    r = session.get(url, headers=HEADERS, timeout=120)
    r.raise_for_status()
    r.encoding = "utf-8"
    return list(csv.DictReader(io.StringIO(r.text)))


def short_unit(raw):
    key = (raw or "").strip().lower()
    if key in UNIT_MAP:
        return UNIT_MAP[key]
    if "gco" in key:
        return "gCO₂/kWh"
    if "million tonnes" in key:
        return "Mt CO₂e"
    return (raw or "").strip() or ""


# -- Main ------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="OWID energy dataset -> energy_data.json")
    parser.add_argument("--out", default=None, help="Output path (default: energy_data.json next to this script)")
    args = parser.parse_args()

    base_dir = os.path.dirname(os.path.abspath(__file__))
    out_path = args.out or os.path.join(base_dir, "energy_data.json")

    print("\nOWID Energy Exporter")
    print(f"  Source : {CSV_URL}")
    print(f"  Output : {out_path}\n")

    session = requests.Session()

    print("  Downloading codebook ...", flush=True)
    codebook = fetch_csv(session, CODEBOOK_URL)
    cb = {row["column"]: row for row in codebook}

    print("  Downloading dataset  ...", flush=True)
    rows = fetch_csv(session, CSV_URL)
    print(f"    {len(rows):,} rows, {len(rows[0])} columns\n")

    missing = [c for c in KEEP_COLS if c not in rows[0]]
    if missing:
        print(f"[ERROR] columns no longer in the OWID file: {missing}", file=sys.stderr)
        sys.exit(1)

    # -- filter + reshape ------------------------------------------------
    entities = {}          # display name -> {iso, aggregate, rows: {year: {col: val}}}
    latest_year = 0
    dropped_aggs = set()

    for row in rows:
        name = row["country"]
        iso = (row.get("iso_code") or "").strip()
        year = int(row["year"])
        if year < YEAR_MIN:
            continue

        if len(iso) == 3:
            display, is_agg = name, False
        elif name in AGGREGATES:
            display, is_agg, iso = AGGREGATES[name], True, ""
        else:
            if not iso:
                dropped_aggs.add(name)
            continue

        vals = {}
        for col in KEEP_COLS:
            raw = row.get(col, "")
            if raw in ("", "NA"):
                continue
            try:
                v = float(raw)
            except ValueError:
                continue
            vals[col] = int(round(v)) if col in INT_COLS else round(v, 3)

        if not vals:
            continue

        ent = entities.setdefault(display, {"iso": iso or None, "aggregate": is_agg, "rows": {}})
        ent["rows"][year] = vals
        latest_year = max(latest_year, year)

    # -- pack per-entity columnar; keep only populated columns ----------
    packed = {}
    for display, ent in entities.items():
        years = sorted(ent["rows"])
        cols_present = [c for c in KEEP_COLS if any(c in ent["rows"][y] for y in years)]
        series = {
            c: [ent["rows"][y].get(c) for y in years]
            for c in cols_present
        }
        packed[display] = {
            "iso": ent["iso"],
            "aggregate": ent["aggregate"],
            "years": years,
            "series": series,
        }

    labels = {c: (cb.get(c, {}).get("title") or c) for c in KEEP_COLS}
    units = {c: short_unit(cb.get(c, {}).get("unit")) for c in KEEP_COLS}
    # per-capita fuel/rollup columns inherit a cleaner token than the generic map
    for c in KEEP_COLS:
        if c.endswith("_share_elec") or c.endswith("_share_energy") or c in (
            "energy_cons_change_pct", "net_elec_imports_share_demand"
        ):
            units[c] = "%"

    output = {
        "generated": datetime.now().strftime("%Y-%m-%d"),
        "source": "Our World in Data — Energy dataset",
        "source_url": "https://github.com/owid/energy-data",
        "csv_url": CSV_URL,
        "licence": "Creative Commons BY — free to use, modify and distribute (OWID energy-data repo)",
        "note": (
            "Rebuilt by OWID from the Energy Institute Statistical Review of World Energy, "
            "Ember, the U.S. EIA and OWID population/GDP series. Detailed fuel and "
            "carbon-intensity coverage begins ~1965 (energy) / ~2000 (electricity mix, "
            "carbon intensity). Aggregates are OWID-defined; do not sum them."
        ),
        "year_min": YEAR_MIN,
        "latest_year": latest_year,
        "labels": labels,
        "units": units,
        "fuels": [
            {"key": k, "label": lab, "color": col,
             "twh_energy": te, "share_energy": se, "twh_elec": tl, "share_elec": sl}
            for (k, lab, col, te, se, tl, sl) in FUELS
        ],
        "metric_groups": [{"label": lab, "cols": cols} for lab, cols in METRIC_GROUPS],
        "entities": packed,
    }

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, separators=(",", ":"))

    n_countries = sum(1 for e in packed.values() if not e["aggregate"])
    n_aggs = sum(1 for e in packed.values() if e["aggregate"])
    n_points = sum(len(v) for e in packed.values() for v in e["series"].values())
    size_kb = os.path.getsize(out_path) // 1024

    print(f"  Saved     : {out_path}")
    print(f"  Entities  : {n_countries} countries + {n_aggs} aggregates")
    print(f"  Columns   : {len(KEEP_COLS)} kept")
    print(f"  Points    : {n_points:,}")
    print(f"  Size      : {size_kb} KB")
    print(f"  Through   : {latest_year}")
    print(f"  Generated : {output['generated']}")
    if dropped_aggs:
        print(f"  Dropped {len(dropped_aggs)} source-internal aggregates "
              f"(e.g. {', '.join(sorted(dropped_aggs)[:4])} ...)")
    print()


if __name__ == "__main__":
    main()
