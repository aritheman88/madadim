"""
fetch_bls_food_cpi.py
=======================
Downloads full history for a curated list of detailed U.S. CPI-U food item
indices from the BLS Public Data API (api.bls.gov) and writes
bls_food_cpi.json to the same directory for use by index.html (madadim.net).

This tracks the same underlying government data shown on paid resellers like
economy.com's "CPI Urban Consumer Butter and Margarine" chart -- BLS itself
publishes it for free, no scraping needed.

Series IDs follow BLS's standard construction: CUUR0000 + item_code, where
CU = CPI for Urban Consumers, U = not seasonally adjusted, 0000 = U.S. city
average. Item codes were taken from BLS's own published catalog
(https://download.bls.gov/pub/time.series/cu/cu.item) rather than assumed --
some items only exist as more granular "item stratum" codes (prefixed SS)
one level below the commonly-cited aggregate (prefixed SEF), e.g. standalone
"Rice" (SS01031) vs. the aggregate "Rice, pasta, cornmeal" (SEFA03). Both are
included below where they add something distinct.

Several commonly-requested items are NOT in this list because BLS simply
doesn't track them as standalone CPI items: tea and chocolate/cocoa have no
dedicated code (only bundled into broader categories), and soybeans/corn
aren't CPI items at all since they're raw agricultural commodities, not
retail grocery purchases -- those are already covered on the site via the
Yahoo futures and World Bank Pink Sheet data instead.

Requires a BLS API key in `.env` at the repo root as `bls_key`. Register a
free key (raises the rate limit from 25 to 500 series/day) at
https://data.bls.gov/registrationEngine/.

Run any time you want fresh data:
    python fetch_bls_food_cpi.py

Requirements:
    pip install requests
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime

import requests

# ── Config ────────────────────────────────────────────────────────────────────

API_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"

# item_code -> (display name, category)
ITEMS = {
    # Aggregates
    "SAF1":    ("Food", "Aggregate"),
    "SAF11":   ("Food at home", "Aggregate"),
    # Cereals & bakery
    "SEFA01":  ("Flour and prepared flour mixes", "Cereals & Bakery"),
    "SEFA02":  ("Breakfast cereal", "Cereals & Bakery"),
    "SEFA03":  ("Rice, pasta, cornmeal", "Cereals & Bakery"),
    "SS01031": ("Rice", "Cereals & Bakery"),
    "SEFB01":  ("Bread", "Cereals & Bakery"),
    # Meat, poultry, fish, eggs
    "SEFC":    ("Beef and veal", "Meat, Poultry, Fish & Eggs"),
    "SEFD":    ("Pork", "Meat, Poultry, Fish & Eggs"),
    "SEFF01":  ("Chicken", "Meat, Poultry, Fish & Eggs"),
    "SEFG":    ("Fish and seafood", "Meat, Poultry, Fish & Eggs"),
    "SEFH":    ("Eggs", "Meat, Poultry, Fish & Eggs"),
    # Dairy
    "SEFJ01":  ("Milk", "Dairy"),
    "SEFJ02":  ("Cheese and related products", "Dairy"),
    # Produce
    "SEFK":    ("Fresh fruits", "Produce"),
    "SEFL":    ("Fresh vegetables", "Produce"),
    "SS14011": ("Frozen vegetables", "Produce"),
    # Beverages, sweets, fats
    "SEFP01":  ("Coffee", "Beverages, Sweets & Fats"),
    "SEFR01":  ("Sugar and sugar substitutes", "Beverages, Sweets & Fats"),
    "SEFS01":  ("Butter and margarine", "Beverages, Sweets & Fats"),
    "SS10011": ("Butter", "Beverages, Sweets & Fats"),
    "SEFS03":  ("Other fats and oils including peanut butter", "Beverages, Sweets & Fats"),
    "SS16014": ("Peanut butter", "Beverages, Sweets & Fats"),
}

FIRST_YEAR = 1913
CHUNK_YEARS = 19  # BLS API v2 caps a single request at a 20-year span


def read_env_value(path, key):
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            if k.strip() == key:
                return v.strip().strip("'").strip('"')
    raise KeyError(f"'{key}' not found in {path}")


def year_chunks(first_year: int, last_year: int, span: int):
    start = first_year
    while start <= last_year:
        end = min(start + span - 1, last_year)
        yield str(start), str(end)
        start = end + 1


# ── Fetch ─────────────────────────────────────────────────────────────────────

def fetch_all(bls_key: str) -> dict:
    """Returns {item_code: [{date, value}, ...]}."""
    series_ids = [f"CUUR0000{code}" for code in ITEMS]
    by_code: dict[str, list[dict]] = {code: [] for code in ITEMS}
    last_year = datetime.now().year

    for start, end in year_chunks(FIRST_YEAR, last_year, CHUNK_YEARS):
        print(f"  fetching {start}-{end} ...", end=" ", flush=True)
        payload = {
            "seriesid": series_ids,
            "startyear": start,
            "endyear": end,
            "registrationkey": bls_key,
        }
        r = requests.post(API_URL, json=payload, timeout=60)
        r.raise_for_status()
        d = r.json()
        if d.get("status") != "REQUEST_SUCCEEDED":
            print(f"[ERROR] {d.get('status')}: {d.get('message')}")
            continue

        n_points = 0
        for series in d.get("Results", {}).get("series", []):
            code = series["seriesID"].replace("CUUR0000", "")
            for row in series["data"]:
                if row["period"] == "M13":  # annual average, not a month
                    continue
                if not row["period"].startswith("M"):
                    continue
                try:
                    value = float(row["value"])
                except (TypeError, ValueError):
                    continue
                date = f"{row['year']}-{row['period'][1:]}"
                by_code[code].append({"date": date, "value": value})
                n_points += 1
        print(f"{n_points} points")
        time.sleep(0.3)

    return by_code


# ── Year-on-year % change ─────────────────────────────────────────────────────

def add_pct_year(series_list: list[dict]) -> None:
    for s in series_list:
        by_date = {r["date"]: r["value"] for r in s["data"]}
        for row in s["data"]:
            yr, mo = row["date"].split("-")
            prev_date = f"{int(yr) - 1}-{mo}"
            prev_val = by_date.get(prev_date)
            if prev_val and prev_val > 0:
                row["pct_year"] = round((row["value"] / prev_val - 1) * 100, 2)
            else:
                row["pct_year"] = None


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="BLS CPI-U food item indices -> bls_food_cpi.json"
    )
    parser.add_argument(
        "--out", default=None,
        help="Output path (default: bls_food_cpi.json next to this script)",
    )
    args = parser.parse_args()

    base_dir = os.path.dirname(os.path.abspath(__file__))
    out_path = args.out or os.path.join(base_dir, "bls_food_cpi.json")
    env_path = os.path.join(base_dir, ".env")

    print("\nBLS CPI-U Food Updater")
    print(f"  Output : {out_path}\n")

    try:
        bls_key = read_env_value(env_path, "bls_key")
    except KeyError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(1)

    by_code = fetch_all(bls_key)

    series_list = []
    for code, (name, category) in ITEMS.items():
        data = sorted(by_code.get(code, []), key=lambda r: r["date"])
        # De-dupe (chunk boundaries shouldn't overlap, but be defensive)
        seen = {}
        for row in data:
            seen[row["date"]] = row["value"]
        data = [{"date": d, "value": round(v, 3)} for d, v in sorted(seen.items())]
        if not data:
            print(f"  [WARN] no data for {code} ({name})")
            continue
        series_list.append({
            "code":     f"BLS_{code}",
            "name":     f"{name} (US CPI)",
            "unit":     "US CPI-U Index",
            "category": category,
            "data":     data,
        })

    if not series_list:
        print("\n[ERROR] No series fetched successfully.", file=sys.stderr)
        sys.exit(1)

    print("\nComputing year-on-year % changes...")
    add_pct_year(series_list)

    last_date = max(
        (row["date"] for s in series_list for row in s["data"]),
        default="unknown",
    )
    output = {
        "updated":    datetime.now().strftime("%Y-%m-%d"),
        "last_date":  last_date,
        "source":     "U.S. Bureau of Labor Statistics, CPI-U (not seasonally adjusted, U.S. city average)",
        "source_url": "https://www.bls.gov/cpi/",
        "series":     series_list,
    }

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, separators=(",", ":"))

    total_pts = sum(len(s["data"]) for s in series_list)
    size_kb = os.path.getsize(out_path) // 1024

    print(f"\n  Saved  : {out_path}")
    print(f"  Series : {len(series_list)}")
    print(f"  Points : {total_pts:,}")
    print(f"  Size   : {size_kb} KB")
    print(f"  Through: {last_date}")
    print(f"  Run on : {output['updated']}\n")


if __name__ == "__main__":
    main()
