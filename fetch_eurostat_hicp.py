#!/usr/bin/env python3
"""
Fetch Eurostat HICP (prc_hicp_midx) bulk data and shrink it down to the
product x country combinations the CBS Price Index Explorer cares about,
writing eurostat_hicp.json in the same {code, name, data:[{date,value,pct_year}]}
shape as pink_sheet.json / boi_rates.json so index.html can load it identically.

Run this periodically (cron / manual) and re-deploy the JSON alongside index.html.
Matches the style of your other CBS/Noga/kimonaim ingestion scripts: requests +
gzip + plain dict parsing, no heavy dependencies needed.

Usage:
    pip install requests --break-system-packages   # if not already installed
    python3 fetch_eurostat_hicp.py
"""
import gzip
import io
import json
import sys
from datetime import datetime, timezone

import requests

TSV_URL = "https://ec.europa.eu/eurostat/api/dissemination/sdmx/2.1/data/prc_hicp_midx?format=TSV&compressed=true"

# ECOICOP product codes worth indexing, mirroring EUROSTAT_PRODUCTS in index.html.
# Extend this list as you find more categories you want comparators for.
#
# Verified against Eurostat's live COICOP codelist (ESTAT:COICOP(6.3), pulled from
# the prc_hicp_midx DSD on 2026-08-18) — the 5-digit "CPxxxxx" codes below are the
# finest leaf-level breakdown HICP actually publishes. Products the user asked for
# that do NOT exist as HICP leaf codes (Wheat, Soybeans — HICP tracks retail/
# consumer purchases, not raw commodities) are covered instead by
# fetch_eurostat_agri.py against the apri_ap_crpouta/apri_ap_anouta producer-price
# datasets. Rice, Coffee, Tea, and Cocoa DO exist as HICP leaf codes, so they stay
# here (monthly, index-based — better resolution than the annual producer prices).
PRODUCTS = {
    "CP00":    "All-items HICP",
    "CP011":   "Food",
    "CP01111": "Rice",
    "CP01141": "Fresh whole milk",
    "CP01147": "Eggs",
    "CP01151": "Butter",
    "CP01153": "Olive oil",
    "CP01174": "Potatoes",
    "CP01181": "Sugar",
    "CP01182": "Jams, marmalades and honey",
    "CP01183": "Chocolate",
    "CP01211": "Coffee",
    "CP01212": "Tea",
    "CP01213": "Cocoa and powdered chocolate",
    "CP0112":  "Bread and cereals",
    "CP0113":  "Meat",
    "CP0114":  "Fish and seafood",
    "CP0115":  "Milk, cheese and eggs",
    "CP0116":  "Oils and fats",
    "CP0117":  "Fruit",
    "CP0121":  "Coffee, tea and cocoa",
    "CP0451":  "Electricity",
    "CP0452":  "Gas",
    "CP0722":  "Fuels and lubricants for personal transport",
}

# Countries/aggregates to keep. 'EA' and 'EU27_2020' included as benchmarks.
GEO = {
    "AT": "Austria", "BE": "Belgium", "BG": "Bulgaria", "CH": "Switzerland",
    "CY": "Cyprus", "CZ": "Czechia", "DE": "Germany", "DK": "Denmark",
    "EE": "Estonia", "EL": "Greece", "ES": "Spain", "FI": "Finland",
    "FR": "France", "HR": "Croatia", "HU": "Hungary", "IE": "Ireland",
    "IS": "Iceland", "IT": "Italy", "LT": "Lithuania", "LU": "Luxembourg",
    "LV": "Latvia", "MT": "Malta", "NL": "Netherlands", "NO": "Norway",
    "PL": "Poland", "PT": "Portugal", "RO": "Romania", "RS": "Serbia",
    "SE": "Sweden", "SI": "Slovenia", "SK": "Slovakia", "TR": "Turkey",
    "UK": "United Kingdom",
    "EA": "Euro area", "EU27_2020": "EU (27 countries)",
}

UNIT_WANTED = "I15"  # index, 2015=100
FREQ_WANTED = "M"    # monthly


def fetch_tsv_lines():
    print(f"Downloading {TSV_URL} ...", file=sys.stderr)
    resp = requests.get(TSV_URL, timeout=180)
    resp.raise_for_status()
    with gzip.GzipFile(fileobj=io.BytesIO(resp.content)) as gz:
        text = gz.read().decode("utf-8")
    lines = text.split("\n")
    print(f"Downloaded and decompressed: {len(lines)} lines", file=sys.stderr)
    return lines


def parse_obs(raw):
    """Parse a single tab-delimited observation cell like '171.44 p' or ': ' or '86.68'."""
    raw = raw.strip()
    if not raw or raw == ":":
        return None
    # status flags are space-separated after the value, e.g. "203.22 b"
    parts = raw.split(" ")
    try:
        return float(parts[0])
    except ValueError:
        return None


def main():
    lines = fetch_tsv_lines()
    if not lines:
        print("No data received — aborting.", file=sys.stderr)
        sys.exit(1)

    header = lines[0].split("\t")
    # header[0] looks like "freq,unit,coicop,geo\TIME_PERIOD"
    key_spec = header[0].split("\\")[0]  # "freq,unit,coicop,geo"
    dim_names = key_spec.split(",")
    periods = [h.strip() for h in header[1:]]  # column labels = YYYY-MM (or YYYY)

    if dim_names != ["freq", "unit", "coicop", "geo"]:
        print(f"WARNING: unexpected dimension order {dim_names} — "
              f"verify parsing logic still matches Eurostat's current TSV header.",
              file=sys.stderr)

    series_out = []
    matched = 0
    for line in lines[1:]:
        if not line.strip():
            continue
        cols = line.split("\t")
        key = cols[0].strip()
        dims = key.split(",")
        if len(dims) != 4:
            continue
        freq, unit, coicop, geo = [d.strip() for d in dims]

        if freq != FREQ_WANTED or unit != UNIT_WANTED:
            continue
        if coicop not in PRODUCTS or geo not in GEO:
            continue

        rows = []
        values_by_period = {}
        for period, obs_raw in zip(periods, cols[1:]):
            val = parse_obs(obs_raw)
            if val is None:
                continue
            # normalize "2015" (annual, shouldn't appear for freq=M, but guard anyway)
            date = period if len(period) == 7 else None
            if date is None:
                continue
            values_by_period[date] = val

        ordered_dates = sorted(values_by_period.keys())
        for i, date in enumerate(ordered_dates):
            val = values_by_period[date]
            pct_year = None
            # find value ~12 months back by date arithmetic, not array index,
            # since there can be gaps in the series
            y, m = int(date[:4]), int(date[5:7])
            prev_y, prev_m = y - 1, m
            prev_date = f"{prev_y:04d}-{prev_m:02d}"
            if prev_date in values_by_period and values_by_period[prev_date]:
                pct_year = round((val / values_by_period[prev_date] - 1) * 100, 2)
            rows.append({
                "date": date,
                "value": val,
                "pct": None,
                "pct_year": pct_year,
            })

        if not rows:
            continue

        code = f"EUROSTAT_{coicop}_{geo}"
        name = f"{PRODUCTS[coicop]} — {GEO[geo]}"
        series_out.append({
            "code": code,
            "name": name,
            "category": "Eurostat HICP",
            "unit": "Index, 2015=100",
            "data": rows,
        })
        matched += 1

    print(f"Matched {matched} series (out of {len(PRODUCTS)} products x {len(GEO)} geos "
          f"= {len(PRODUCTS) * len(GEO)} possible combos)", file=sys.stderr)

    out = {
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "Eurostat prc_hicp_midx (HICP monthly index, 2015=100)",
        "series": series_out,
    }

    with open("eurostat_hicp.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))

    print(f"Wrote eurostat_hicp.json ({len(series_out)} series)", file=sys.stderr)


if __name__ == "__main__":
    main()