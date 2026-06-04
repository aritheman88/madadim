"""
boi_update.py
=============
Downloads USD/NIS and EUR/NIS monthly exchange rates from the Bank of
Israel SDMX API and writes boi_rates.json for use by index.html.

Run any time you want fresh data:
    python boi_update.py

Requirements:
    pip install requests
"""

import argparse
import json
import os
import sys
from datetime import datetime

import requests

BOI_BASE = "https://edge.boi.org.il/FusionEdgeServer/sdmx/v2/data/dataflow/BOI.STATISTICS/EXR/1.0"

SERIES = [
    {"code": "BOI_USD_ILS", "name": "USD / NIS", "series_id": "RER_USD_ILS"},
    {"code": "BOI_EUR_ILS", "name": "EUR / NIS", "series_id": "RER_EUR_ILS"},
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}


def fetch_series(series_id: str) -> list[dict]:
    url = f"{BOI_BASE}/{series_id}?format=csv&normalisefreq=M;mean"
    print(f"  GET {url}")
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()

    lines = r.text.strip().splitlines()
    if len(lines) < 2:
        raise ValueError(f"Empty response for {series_id}")

    headers = lines[0].split(",")
    t_idx = headers.index("TIME_PERIOD")
    v_idx = headers.index("OBS_VALUE")

    rows = []
    for line in lines[1:]:
        cols = line.split(",")
        date = cols[t_idx].strip()
        try:
            val = float(cols[v_idx])
        except (ValueError, IndexError):
            continue
        if date and val > 0:
            rows.append({"date": date, "value": round(val, 6), "pct_year": None})

    rows.sort(key=lambda x: x["date"])

    # Compute YoY %
    by_date = {r["date"]: r["value"] for r in rows}
    for row in rows:
        yr, mo = row["date"].split("-")
        prev = by_date.get(f"{int(yr) - 1}-{mo}")
        if prev:
            row["pct_year"] = round((row["value"] / prev - 1) * 100, 2)

    return rows


def main():
    parser = argparse.ArgumentParser(description="Bank of Israel exchange rates → boi_rates.json")
    parser.add_argument("--out", default=None,
                        help="Output path (default: boi_rates.json next to this script)")
    args = parser.parse_args()

    base_dir = os.path.dirname(os.path.abspath(__file__))
    out_path = args.out or os.path.join(base_dir, "boi_rates.json")

    print("\nBank of Israel Exchange Rate Updater")
    print(f"  Output: {out_path}\n")

    result = {"updated": datetime.now().strftime("%Y-%m-%d"), "series": []}

    for s in SERIES:
        print(f"[{s['series_id']}] {s['name']}")
        try:
            data = fetch_series(s["series_id"])
            print(f"  {len(data)} monthly observations  ({data[0]['date']} to {data[-1]['date']})")
            result["series"].append({
                "code":     s["code"],
                "name":     s["name"],
                "unit":     "₪",
                "category": "Exchange Rates",
                "data":     data,
            })
        except Exception as e:
            print(f"  [ERROR] {e}", file=sys.stderr)
            sys.exit(1)

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, separators=(",", ":"))

    size_kb = os.path.getsize(out_path) // 1024
    print(f"\n  Saved: {out_path}  ({size_kb} KB)")
    print(f"  Updated: {result['updated']}\n")


if __name__ == "__main__":
    main()
