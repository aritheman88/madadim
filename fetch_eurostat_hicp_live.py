#!/usr/bin/env python3
"""
Fetch Eurostat's CURRENT (live-updating) HICP dataset, prc_hicp_minr, and
shrink it to eurostat_hicp_live.json — same shape as eurostat_hicp.json.

Why this is a separate script/file rather than an update to
fetch_eurostat_hicp.py: Eurostat rebased HICP in Jan 2026 (new base year
2025=100, revised "ECOICOP v2" classification, dataset id prc_hicp_minr) and
discontinued the old dataset prc_hicp_midx that fetch_eurostat_hicp.py pulls
— its last real update was 2026-02-06 and it's permanently frozen at
2025-12. eurostat_hicp.json stays as a frozen historical snapshot (and keeps
its finer product breakdown — see below); this script covers the same
category *positions* going forward on the new dataset, best-effort.

IMPORTANT — this is NOT a continuation of the same series. ECOICOP v2
renumbered categories, so e.g. code CP01111 meant "Rice" under the old
classification (COICOP v1) but means combined "Cereals" (rice+wheat+
barley+maize+etc, undifferentiated) under the new one (COICOP18). Verified
by cross-referencing Eurostat's live COICOP18 codelist against which codes
actually have published data (many leaf codes exist in the classification
schema with zero real observations) on 2026-08-18:
  - Milk, Eggs, Sugar, Coffee, Tea: published with full country coverage,
    each at a similar granularity to before (though Coffee/Tea now bundle
    in "substitutes"/"maté & herbal infusions" respectively).
  - Chocolate and Cocoa: no longer separable — merged into one combined
    "Chocolate, cocoa and cocoa-based food products" index.
  - Butter: no longer separable — merged into "Butter and other oils/fats
    derived from milk".
  - Olive oil and Potatoes: published, but only for ~1/3 of countries
    (others fold them into broader "vegetable oils" / "tubers" aggregates)
    — expect sparse geo coverage for these two.
  - Rice, Wheat, Soya beans: NOT published as standalone series at all,
    old dataset or new. Wheat/Soya are covered instead by
    fetch_eurostat_agri.py (annual producer prices) — Eurostat simply has
    no monthly consumer-price series for raw wheat/soybeans. Rice used to
    be its own series in the now-frozen prc_hicp_midx (kept in
    eurostat_hicp.json) but has no live equivalent going forward.

Run this periodically (cron / manual) and re-deploy the JSON alongside
index.html, same as fetch_eurostat_hicp.py.

Usage:
    pip install requests --break-system-packages   # if not already installed
    python3 fetch_eurostat_hicp_live.py
"""
import gzip
import io
import json
import sys
from datetime import datetime, timezone

import requests

TSV_URL = "https://ec.europa.eu/eurostat/api/dissemination/sdmx/2.1/data/prc_hicp_minr?format=TSV&compressed=true"

# COICOP18 codes verified (2026-08-18) to have real published monthly data —
# see module docstring for which categories lost granularity vs the old
# dataset. Names spell out "(combined)" wherever the live series bundles
# products that used to be (or still are, historically) separate.
PRODUCTS = {
    "TOTAL":    "All-items HICP",
    "CP011":    "Food",
    "CP01111":  "Cereals (rice, wheat, maize, barley, etc. — combined)",
    "CP0112":   "Meat",
    "CP0113":   "Fish and seafood",
    "CP0114":   "Milk, dairy products and eggs",
    "CP01141":  "Raw and whole milk",
    "CP01148":  "Eggs",
    "CP0115":   "Oils and fats",
    "CP01152":  "Butter and other oils/fats derived from milk (combined)",
    "CP011513": "Olive oil (partial country coverage)",
    "CP0117":   "Vegetables, tubers and pulses",
    "CP011751": "Potatoes (partial country coverage)",
    "CP0118":   "Sugar, confectionery and desserts",
    "CP01181":  "Cane sugar and beet sugar",
    "CP01185":  "Chocolate, cocoa and cocoa-based food products (combined)",
    "CP0122":   "Coffee and coffee substitutes",
    "CP0123":   "Tea, maté and other plant infusions",
    "CP0124":   "Cocoa drinks",
    "CP0451":   "Electricity",
    "CP0452":   "Gas",
    "CP0722":   "Fuels and lubricants for personal transport",
}

# Countries/aggregates to keep, matching fetch_eurostat_hicp.py's GEO dict.
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

UNIT_WANTED = "I25"  # index, 2025=100 — the new base year; deliberately NOT
                     # I15, to avoid implying false continuity with the
                     # frozen eurostat_hicp.json series (see docstring).
FREQ_WANTED = "M"


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
    raw = raw.strip()
    if not raw or raw == ":":
        return None
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
    key_spec = header[0].split("\\")[0]
    dim_names = key_spec.split(",")
    periods = [h.strip() for h in header[1:]]

    if dim_names != ["freq", "unit", "coicop18", "geo"]:
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

        values_by_period = {}
        for period, obs_raw in zip(periods, cols[1:]):
            val = parse_obs(obs_raw)
            if val is None:
                continue
            date = period if len(period) == 7 else None
            if date is None:
                continue
            values_by_period[date] = val

        ordered_dates = sorted(values_by_period.keys())
        rows = []
        for date in ordered_dates:
            val = values_by_period[date]
            pct_year = None
            y, m = int(date[:4]), int(date[5:7])
            prev_date = f"{y-1:04d}-{m:02d}"
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

        # No underscore between LIVE and coicop: index.html's badge regex
        # (^EUROSTAT_[A-Z0-9]+_(.+)$) assumes exactly one underscore-separated
        # geo suffix.
        code = f"EUROSTAT_LIVE{coicop}_{geo}"
        name = f"{PRODUCTS[coicop]} — {GEO[geo]}"
        series_out.append({
            "code": code,
            "name": name,
            "category": "Eurostat HICP (live, 2025=100, ECOICOP v2)",
            "unit": "Index, 2025=100",
            "data": rows,
        })
        matched += 1

    print(f"Matched {matched} series (out of {len(PRODUCTS)} products x {len(GEO)} geos "
          f"= {len(PRODUCTS) * len(GEO)} possible combos)", file=sys.stderr)

    out = {
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "Eurostat prc_hicp_minr (HICP monthly index, 2025=100, ECOICOP v2 — live)",
        "series": series_out,
    }

    with open("eurostat_hicp_live.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))

    print(f"Wrote eurostat_hicp_live.json ({len(series_out)} series)", file=sys.stderr)


if __name__ == "__main__":
    main()
