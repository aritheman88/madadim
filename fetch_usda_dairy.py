"""
fetch_usda_dairy.py
=====================
Downloads full history for a curated list of USDA MARS (My Market News)
"Point of Sale - Dairy" report slugs and writes usda_dairy.json to the same
directory for use by index.html (madadim.net).

Each slug can contain more than one commodity (e.g. slug 1098 "Butter/
Butteroil - Europe" reports both Butter and Butteroil), so series are built
from the actual (commodity, region) combinations found in the data rather
than assumed 1:1 with slugs.

Only USDA MARS's "Point of Sale - Dairy" report family was found to return
clean structured price fields (price_min/price_max) through this API.
Several other categories investigated (egg index, chicken, cotton spot,
grain narrative reports) return prose-only "report_narrative" text with no
extractable numeric fields, so they are NOT included here.

Auth: HTTP Basic, with the API key as the username and a blank password
(USDA MARS's documented scheme) -- see usda/usda_sample.py for the original
exploratory version of this.

Requires a USDA MARS API key in `.env` at the repo root as `usda_key`.
Register for a free key at https://mymarketnews.ams.usda.gov.

Run any time you want fresh data:
    python fetch_usda_dairy.py

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

API_BASE = "https://marsapi.ams.usda.gov/services/v1.2/reports"

# slug_id -> just used for logging; commodity/region/unit all come from the
# data itself, since a single slug can contain more than one series.
SLUGS = [
    1098,  # Butter/Butteroil - Europe
    1099,  # Butter - Oceania
    1092,  # Cheese - Foreign Type
    1085,  # Cheese - West U.S.
    1082,  # Cheese - Oceania
    1036,  # Whole Milk Powder - Europe
    1039,  # Whole Milk Powder - Oceania
    1034,  # Dry Whey - Europe
    1046,  # Dry Whey - West U.S.
    1049,  # Nonfat Dry Milk - East and Central U.S.
    1051,  # Casein - U.S.
]


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


def make_code(slug_id: int, commodity: str, region: str) -> str:
    clean = f"{commodity}_{region}"
    clean = "".join(c if c.isalnum() else "_" for c in clean).upper()
    while "__" in clean:
        clean = clean.replace("__", "_")
    return f"USDA_{slug_id}_{clean.strip('_')}"


def parse_date(mmddyyyy: str) -> str:
    dt = datetime.strptime(mmddyyyy, "%m/%d/%Y")
    return dt.strftime("%Y-%m")


def row_value(row: dict) -> float | None:
    lo, hi = row.get("price_min"), row.get("price_max")
    if lo is not None and hi is not None:
        return (float(lo) + float(hi)) / 2
    lo, hi = row.get("mostly_low_price"), row.get("mostly_high_price")
    if lo is not None and hi is not None:
        return (float(lo) + float(hi)) / 2
    return None


# ── Fetch ─────────────────────────────────────────────────────────────────────

def fetch_slug(session: requests.Session, usda_key: str, slug_id: int) -> list[dict]:
    url = f"{API_BASE}/{slug_id}"
    r = session.get(url, auth=(usda_key, ""), timeout=60)
    r.raise_for_status()
    return r.json().get("results", [])


def build_series_for_slug(slug_id: int, rows: list[dict]) -> list[dict]:
    """Group a slug's rows by (commodity, region) into separate series."""
    groups: dict[tuple, list[dict]] = {}
    for row in rows:
        commodity = row.get("commodity")
        region = row.get("region")
        if not commodity or not region or region == "N/A":
            continue
        unit = row.get("price_Unit") or ""
        if "basis" in unit.lower():
            # Basis-priced rows are a cents differential vs. the CME spot
            # price, not an absolute price -- not comparable to the other
            # (absolute-dollar) series here, so skip them.
            continue
        val = row_value(row)
        if val is None:
            continue
        date = parse_date(row["report_date"])
        groups.setdefault((commodity, region), []).append({
            "date": date, "value": val, "unit": row.get("price_Unit") or "",
        })

    # USDA's own historical data occasionally mislabels a handful of rows with
    # a slightly different region string for what is otherwise the same
    # series (e.g. one stray "Europe" row among 861 "West Europe" rows for
    # the same commodity). Fold any tiny region variant (<10 rows) into the
    # dominant region for that commodity rather than splitting off a
    # near-empty phantom series.
    by_commodity: dict[str, list[tuple]] = {}
    for (commodity, region), pts in groups.items():
        by_commodity.setdefault(commodity, []).append((region, pts))
    for commodity, region_groups in by_commodity.items():
        if len(region_groups) < 2:
            continue
        region_groups.sort(key=lambda rg: len(rg[1]), reverse=True)
        dominant_region, _ = region_groups[0]
        for region, pts in region_groups[1:]:
            if len(pts) < 10:
                groups[(commodity, dominant_region)].extend(pts)
                del groups[(commodity, region)]

    series_list = []
    for (commodity, region), points in groups.items():
        # Aggregate to monthly average (source cadence is weekly/biweekly).
        by_month: dict[str, list[float]] = {}
        for p in points:
            by_month.setdefault(p["date"], []).append(p["value"])
        data = [
            {"date": d, "value": round(sum(vs) / len(vs), 4)}
            for d, vs in sorted(by_month.items())
        ]
        series_list.append({
            "code":     make_code(slug_id, commodity, region),
            "name":     f"{commodity} ({region})",
            "unit":     points[0]["unit"],
            "category": "Dairy",
            "slug_id":  slug_id,
            "data":     data,
        })
    return series_list


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
        description="USDA MARS dairy Point-of-Sale reports -> usda_dairy.json"
    )
    parser.add_argument(
        "--out", default=None,
        help="Output path (default: usda_dairy.json next to this script)",
    )
    args = parser.parse_args()

    base_dir = os.path.dirname(os.path.abspath(__file__))
    out_path = args.out or os.path.join(base_dir, "usda_dairy.json")
    env_path = os.path.join(base_dir, ".env")

    print("\nUSDA MARS Dairy Updater")
    print(f"  Output : {out_path}\n")

    try:
        usda_key = read_env_value(env_path, "usda_key")
    except KeyError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(1)

    session = requests.Session()
    all_series = []

    for slug_id in SLUGS:
        print(f"  slug {slug_id:6d} ... ", end="", flush=True)
        try:
            rows = fetch_slug(session, usda_key, slug_id)
        except requests.exceptions.HTTPError as e:
            print(f"[ERROR] HTTP {e.response.status_code}")
            continue
        except Exception as e:
            print(f"[ERROR] {e}")
            continue

        series_list = build_series_for_slug(slug_id, rows)
        if not series_list:
            print(f"{len(rows)} rows, [no structured series found]")
        else:
            names = ", ".join(s["name"] for s in series_list)
            print(f"{len(rows)} rows -> {len(series_list)} series ({names})")
        all_series.extend(series_list)
        time.sleep(0.3)  # be polite

    if not all_series:
        print("\n[ERROR] No series fetched successfully.", file=sys.stderr)
        sys.exit(1)

    print("\nComputing year-on-year % changes...")
    add_pct_year(all_series)

    last_date = max(
        (row["date"] for s in all_series for row in s["data"]),
        default="unknown",
    )
    output = {
        "updated":    datetime.now().strftime("%Y-%m-%d"),
        "last_date":  last_date,
        "source":     "USDA MARS (My Market News) - Point of Sale Dairy reports",
        "source_url": "https://mymarketnews.ams.usda.gov",
        "series":     all_series,
    }

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, separators=(",", ":"))

    total_pts = sum(len(s["data"]) for s in all_series)
    size_kb = os.path.getsize(out_path) // 1024

    print(f"\n  Saved  : {out_path}")
    print(f"  Series : {len(all_series)}")
    print(f"  Points : {total_pts:,}")
    print(f"  Size   : {size_kb} KB")
    print(f"  Through: {last_date}")
    print(f"  Run on : {output['updated']}\n")


if __name__ == "__main__":
    main()
