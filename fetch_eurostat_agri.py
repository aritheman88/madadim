#!/usr/bin/env python3
"""
Fetch Eurostat agricultural producer prices for raw commodities that HICP
(prc_hicp_midx) does NOT cover — HICP only tracks retail/consumer purchase
categories, so things Europe doesn't sell directly to consumers as such
(Wheat, Soya/Soybeans, Maize, Barley) or that are better sourced at the farm
gate (raw cow's milk) are missing from it entirely. Writes eurostat_agri.json
in the same {code, name, category, unit, data:[{date,value,pct,pct_year}]}
shape as eurostat_hicp.json / pink_sheet.json so index.html can load it
identically.

Datasets used (both annual, national-currency-or-EUR, price per 100 kg):
  - apri_ap_crpouta  "Selling prices of crop products"       -> Wheat, Soya, ...
  - apri_ap_anouta   "Selling prices of animal products"     -> Raw cow's milk

Unlike HICP these are NOT index numbers (base=100) — they're absolute
producer prices in EUR per 100 kg (or per 100 litres for milk/wine), and
they're annual only (no monthly/quarterly Eurostat series exists for these
commodities). Coffee/Tea/Cocoa/Chocolate/Sugar are deliberately NOT
duplicated here: Europe doesn't grow/produce those crops, so Eurostat has no
producer-price series for them — HICP consumer prices (fetch_eurostat_hicp.py)
are the only Eurostat source and already cover them at the leaf-code level.

Run this periodically (cron / manual) and re-deploy the JSON alongside index.html.

Usage:
    pip install requests --break-system-packages   # if not already installed
    python3 fetch_eurostat_agri.py
"""
import json
import sys
from datetime import datetime, timezone

import requests

# Each entry: dataset id, product-dimension name, product code, display name, unit label.
COMMODITIES = [
    ("apri_ap_crpouta", "prod_veg", "01110000", "Wheat (soft)",     "EUR / 100 kg"),
    ("apri_ap_crpouta", "prod_veg", "01120000", "Wheat (durum)",    "EUR / 100 kg"),
    ("apri_ap_crpouta", "prod_veg", "01500000", "Maize (corn)",     "EUR / 100 kg"),
    ("apri_ap_crpouta", "prod_veg", "01300000", "Barley",           "EUR / 100 kg"),
    ("apri_ap_crpouta", "prod_veg", "02130000", "Soya (soybeans)",  "EUR / 100 kg"),
    ("apri_ap_anouta",  "prod_ani", "12111000", "Raw cow's milk",   "EUR / 100 kg"),
]

# Countries/aggregates to keep, matching fetch_eurostat_hicp.py's GEO dict where
# the underlying dataset actually has data. apri_ap_* covers only EU member
# states (+ UK historically, + XK) — no CH/NO/IS/RS/TR/EA/EU27_2020 aggregate.
GEO = {
    "AT": "Austria", "BE": "Belgium", "BG": "Bulgaria", "CY": "Cyprus",
    "CZ": "Czechia", "DE": "Germany", "DK": "Denmark", "EE": "Estonia",
    "EL": "Greece", "ES": "Spain", "FI": "Finland", "FR": "France",
    "HR": "Croatia", "HU": "Hungary", "IE": "Ireland", "IT": "Italy",
    "LT": "Lithuania", "LU": "Luxembourg", "LV": "Latvia", "MT": "Malta",
    "NL": "Netherlands", "PL": "Poland", "PT": "Portugal", "RO": "Romania",
    "SE": "Sweden", "SI": "Slovenia", "SK": "Slovakia", "UK": "United Kingdom",
}

BASE_URL = "https://ec.europa.eu/eurostat/api/dissemination/sdmx/2.1/data"


def fetch_dataset_json(dataset, prod_dim, prod_code):
    url = f"{BASE_URL}/{dataset}?format=JSON&{prod_dim}={prod_code}&currency=EUR&lang=en"
    print(f"Downloading {dataset} / {prod_code} ...", file=sys.stderr)
    resp = requests.get(url, timeout=180)
    resp.raise_for_status()
    return resp.json()


def parse_series(js, geo_code):
    """Extract one geo's annual time series from a JSON-stat 2.0 payload."""
    dims = js["dimension"]
    dim_order = js["id"]  # e.g. ["freq","currency","prod_veg","geo","time"]
    sizes = js["size"]

    geo_index = dims["geo"]["category"]["index"].get(geo_code)
    if geo_index is None:
        return None
    time_cat = dims["time"]["category"]["index"]
    ordered_times = sorted(time_cat.keys(), key=lambda t: time_cat[t])

    # Every non-time, non-geo dimension here has exactly one selected value
    # (we queried a single prod code and currency=EUR), so its index is 0.
    def dim_pos(name, idx):
        coords = [0] * len(dim_order)
        coords[dim_order.index("geo")] = idx
        return coords

    values = js["value"]
    strides = [1] * len(dim_order)
    for i in range(len(dim_order) - 2, -1, -1):
        strides[i] = strides[i + 1] * sizes[i + 1]

    rows = []
    values_by_year = {}
    time_pos = dim_order.index("time")
    geo_pos = dim_order.index("geo")
    for t_idx, year in enumerate(ordered_times):
        coords = [0] * len(dim_order)
        coords[geo_pos] = geo_index
        coords[time_pos] = t_idx
        flat = sum(c * s for c, s in zip(coords, strides))
        val = values.get(str(flat)) if isinstance(values, dict) else (
            values[flat] if flat < len(values) else None)
        if val is None:
            continue
        values_by_year[year] = val

    ordered_years = sorted(values_by_year.keys())
    for year in ordered_years:
        val = values_by_year[year]
        pct_year = None
        prev_year = str(int(year) - 1)
        if prev_year in values_by_year and values_by_year[prev_year]:
            pct_year = round((val / values_by_year[prev_year] - 1) * 100, 2)
        rows.append({
            "date": f"{year}-01",  # annual obs, normalized to Jan like other annual series
            "value": val,
            "pct": None,
            "pct_year": pct_year,
        })
    return rows


def main():
    series_out = []
    for dataset, prod_dim, prod_code, prod_name, unit in COMMODITIES:
        try:
            js = fetch_dataset_json(dataset, prod_dim, prod_code)
        except requests.RequestException as e:
            print(f"  FAILED {dataset}/{prod_code}: {e}", file=sys.stderr)
            continue

        for geo_code, geo_name in GEO.items():
            rows = parse_series(js, geo_code)
            if not rows:
                continue
            # No underscore between AGRI and prod_code: index.html's badge regex
            # (^EUROSTAT_[A-Z0-9]+_(.+)$) assumes exactly one underscore-separated
            # geo suffix, same as the EUROSTAT_{coicop}_{geo} codes from
            # fetch_eurostat_hicp.py.
            code = f"EUROSTAT_AGRI{prod_code}_{geo_code}"
            name = f"{prod_name} — {geo_name}"
            series_out.append({
                "code": code,
                "name": name,
                "category": "Eurostat Agricultural Producer Prices",
                "unit": unit,
                "data": rows,
            })
        print(f"  {prod_name}: matched {sum(1 for s in series_out if s['code'].startswith(f'EUROSTAT_AGRI{prod_code}_'))} geos",
              file=sys.stderr)

    out = {
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "Eurostat apri_ap_crpouta / apri_ap_anouta (annual producer prices, EUR/100kg)",
        "series": series_out,
    }

    with open("eurostat_agri.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))

    print(f"Wrote eurostat_agri.json ({len(series_out)} series)", file=sys.stderr)


if __name__ == "__main__":
    main()
