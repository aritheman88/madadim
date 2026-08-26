"""
fetch_yahoo_futures.py
=======================
Downloads monthly settlement prices for a curated list of commodity futures
contracts (energy, metals, grains/oilseeds, softs, livestock & dairy) from
Yahoo Finance's public chart endpoint, and writes futures.json to the same
directory for use by index.html (madadim.net).

This is NOT scraped from investing.com — investing.com has no public API and
its terms of use explicitly prohibit automated extraction/redistribution of
its data. Yahoo Finance's chart endpoint (query1.finance.yahoo.com) is free,
requires no API key, and is the same data source underlying the widely-used
open-source `yfinance` library. It has no documented SLA/ToS guarantee, so
treat this as "best-effort, may need adjusting if Yahoo changes the endpoint."

Unlike the World Bank Pink Sheet (real physical trade prices, averaged
monthly), this dataset is exchange futures *settlement* prices — forward
market prices, one bar per calendar month (Yahoo's own `interval=1mo`
aggregation, taken at the contract that was front-month/continuous at fetch
time). It complements, not replaces, the Pink Sheet series already on the
site.

Run any time you want fresh data:
    python fetch_yahoo_futures.py

Override the output path:
    python fetch_yahoo_futures.py --out /path/to/futures.json

Requirements:
    pip install requests
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

import requests

# ── Config ────────────────────────────────────────────────────────────────────

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}

# (Yahoo symbol, display name, category, unit)
SYMBOLS = [
    # ── Energy ──────────────────────────────────────────────────────────────
    ("CL=F",  "Crude Oil (WTI)",       "Energy", "USD/bbl"),
    ("BZ=F",  "Crude Oil (Brent)",     "Energy", "USD/bbl"),
    ("NG=F",  "Natural Gas (Henry Hub)", "Energy", "USD/MMBtu"),
    ("HO=F",  "Heating Oil (NY Harbor)", "Energy", "USD/gal"),
    ("RB=F",  "Gasoline (RBOB)",       "Energy", "USD/gal"),
    # ── Precious metals ─────────────────────────────────────────────────────
    ("GC=F",  "Gold",                  "Metals", "USD/oz t"),
    ("SI=F",  "Silver",                "Metals", "USD/oz t"),
    ("PL=F",  "Platinum",              "Metals", "USD/oz t"),
    ("PA=F",  "Palladium",             "Metals", "USD/oz t"),
    # ── Industrial metals ───────────────────────────────────────────────────
    ("HG=F",  "Copper",                "Metals", "USD/lb"),
    ("ALI=F", "Aluminum",              "Metals", "USD/mt"),
    # ── Grains & oilseeds ────────────────────────────────────────────────────
    ("ZC=F",  "Corn",                  "Agriculture", "cents/bu"),
    ("ZW=F",  "Wheat (Chicago)",       "Agriculture", "cents/bu"),
    ("KE=F",  "Wheat (Kansas City)",   "Agriculture", "cents/bu"),
    ("ZS=F",  "Soybeans",              "Agriculture", "cents/bu"),
    ("ZL=F",  "Soybean Oil",           "Agriculture", "cents/lb"),
    ("ZM=F",  "Soybean Meal",          "Agriculture", "USD/short ton"),
    ("ZO=F",  "Oats",                  "Agriculture", "cents/bu"),
    ("ZR=F",  "Rough Rice",            "Agriculture", "USD/cwt"),
    # ── Softs ────────────────────────────────────────────────────────────────
    ("KC=F",  "Coffee",                "Agriculture", "cents/lb"),
    ("SB=F",  "Sugar #11",             "Agriculture", "cents/lb"),
    ("CC=F",  "Cocoa",                 "Agriculture", "USD/mt"),
    ("CT=F",  "Cotton",                "Agriculture", "cents/lb"),
    ("OJ=F",  "Orange Juice",          "Agriculture", "cents/lb"),
    # ── Livestock & dairy ────────────────────────────────────────────────────
    ("LE=F",  "Live Cattle",           "Agriculture", "cents/lb"),
    ("GF=F",  "Feeder Cattle",         "Agriculture", "cents/lb"),
    ("HE=F",  "Lean Hogs",             "Agriculture", "cents/lb"),
    ("DC=F",  "Milk (Class III)",      "Agriculture", "USD/cwt"),
    # ── Other ────────────────────────────────────────────────────────────────
    ("LBR=F", "Lumber",                "Other", "USD/1000 bd ft"),
]


def make_code(symbol: str) -> str:
    return "FUT_" + symbol.replace("=F", "").upper()


# ── Fetch ─────────────────────────────────────────────────────────────────────

def fetch_symbol(session: requests.Session, symbol: str) -> list[dict]:
    """Fetch full-history monthly settlement bars for one Yahoo futures symbol."""
    url = CHART_URL.format(symbol=symbol)
    params = {"range": "max", "interval": "1mo"}
    r = session.get(url, headers=HEADERS, params=params, timeout=30)
    r.raise_for_status()
    js = r.json()

    result = (js.get("chart") or {}).get("result")
    if not result:
        err = (js.get("chart") or {}).get("error")
        raise ValueError(f"No result for {symbol}: {err}")

    r0 = result[0]
    timestamps = r0.get("timestamp") or []
    quote = (r0.get("indicators") or {}).get("quote") or [{}]
    closes = quote[0].get("close") or []

    rows = []
    for ts, close in zip(timestamps, closes):
        if close is None:
            continue
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        rows.append({"date": dt.strftime("%Y-%m"), "value": round(float(close), 4)})

    # Yahoo month bars are keyed by bar-open date; de-duplicate by month
    # (keep the last value seen for a given month, in case of overlap).
    by_month = {row["date"]: row["value"] for row in rows}
    rows = [{"date": d, "value": v} for d, v in sorted(by_month.items())]
    return rows


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
        description="Yahoo Finance commodity futures -> futures.json"
    )
    parser.add_argument(
        "--out", default=None,
        help="Output path (default: futures.json next to this script)",
    )
    args = parser.parse_args()

    base_dir = os.path.dirname(os.path.abspath(__file__))
    out_path = args.out or os.path.join(base_dir, "futures.json")

    print("\nYahoo Finance Futures Updater")
    print(f"  Output : {out_path}\n")

    session = requests.Session()
    series_list = []

    for symbol, name, category, unit in SYMBOLS:
        print(f"  {symbol:8s} {name:28s} ", end="", flush=True)
        try:
            data = fetch_symbol(session, symbol)
        except Exception as e:
            print(f"[ERROR] {e}")
            continue
        if not data:
            print("[no data]")
            continue
        print(f"{len(data)} months, through {data[-1]['date']}")
        series_list.append({
            "code":     make_code(symbol),
            "name":     name,
            "unit":     unit,
            "category": category,
            "symbol":   symbol,
            "data":     data,
        })
        time.sleep(0.3)  # be polite

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
        "source":     "Yahoo Finance (monthly futures settlement prices; unofficial free feed)",
        "source_url": "https://finance.yahoo.com",
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
