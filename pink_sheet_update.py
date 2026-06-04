"""
pink_sheet_update.py
====================
Downloads the World Bank Commodity Price Data ("Pink Sheet") monthly
Excel file, parses all commodity series, and writes pink_sheet.json
to the same directory for use by index.html (madadim.net).

Run any time you want fresh data:
    python pink_sheet_update.py

Override the source URL if the World Bank changes the document ID:
    python pink_sheet_update.py --url https://thedocs.worldbank.org/.../CMO-Historical-Data-Monthly.xlsx

Requirements:
    pip install requests openpyxl
"""

import argparse
import io
import json
import os
import re
import sys
from datetime import datetime, date

import requests
import openpyxl

# ── Config ────────────────────────────────────────────────────────────────────

PINK_SHEET_URL = (
    "https://thedocs.worldbank.org/en/doc/"
    "18675f1d1639c7a34d463f59263ba0a2-0050012025/related/"
    "CMO-Historical-Data-Monthly.xlsx"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}

CATEGORIES = [
    ("crude", "Energy"), ("coal", "Energy"), ("natural gas", "Energy"),
    ("lng", "Energy"), ("petrol", "Energy"),
    ("copper", "Metals"), ("aluminum", "Metals"), ("aluminium", "Metals"),
    ("iron ore", "Metals"), ("tin", "Metals"), ("nickel", "Metals"),
    ("zinc", "Metals"), ("lead", "Metals"), ("gold", "Metals"),
    ("silver", "Metals"), ("platinum", "Metals"),
    ("wheat", "Agriculture"), ("maize", "Agriculture"), ("corn", "Agriculture"),
    ("rice", "Agriculture"), ("sorghum", "Agriculture"), ("barley", "Agriculture"),
    ("palm", "Agriculture"), ("soybean", "Agriculture"), ("coconut", "Agriculture"),
    ("groundnut", "Agriculture"), ("sunflower", "Agriculture"),
    ("coffee", "Agriculture"), ("cocoa", "Agriculture"), ("tea", "Agriculture"),
    ("banana", "Agriculture"), ("orange", "Agriculture"), ("sugar", "Agriculture"),
    ("cotton", "Agriculture"), ("rubber", "Agriculture"), ("tobacco", "Agriculture"),
    ("timber", "Agriculture"), ("logs", "Agriculture"), ("sawnwood", "Agriculture"),
    ("dap", "Fertilizers"), ("urea", "Fertilizers"), ("potassium", "Fertilizers"),
    ("tsp", "Fertilizers"), ("phosphate", "Fertilizers"),
]


def categorize(name: str) -> str:
    lower = name.lower()
    for keyword, cat in CATEGORIES:
        if keyword in lower:
            return cat
    return "Other"


def make_code(name: str) -> str:
    clean = re.sub(r"[^a-zA-Z0-9]+", "_", name.strip()).upper().strip("_")
    return f"WB_{clean}"


def parse_date_cell(val) -> str | None:
    """Parse date cells: datetime objects, 'YYYY Mxx', serial floats, etc."""
    if val is None:
        return None
    if isinstance(val, (datetime, date)):
        return val.strftime("%Y-%m")
    s = str(val).strip()
    # "1960 M01" or "1960M01"
    m = re.match(r"(\d{4})\s*[Mm](\d{1,2})$", s)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}"
    # "Jan-60", "Jan-1960", "January 1960"
    for fmt in ("%b-%y", "%b-%Y", "%B %Y", "%B-%Y", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            dt = datetime.strptime(s, fmt)
            if dt.year < 100:
                dt = dt.replace(year=dt.year + (1900 if dt.year >= 60 else 2000))
            return dt.strftime("%Y-%m")
        except ValueError:
            continue
    return None


# ── Download ──────────────────────────────────────────────────────────────────

def download(url: str) -> bytes:
    print(f"  GET {url}")
    r = requests.get(url, headers=HEADERS, timeout=90, stream=True)
    r.raise_for_status()
    total = int(r.headers.get("content-length", 0))
    buf = []
    done = 0
    for chunk in r.iter_content(65536):
        buf.append(chunk)
        done += len(chunk)
        if total:
            print(f"\r  {done // 1024} KB / {total // 1024} KB  ({done/total*100:.0f}%)",
                  end="", flush=True)
    print()
    return b"".join(buf)


# ── Parse ─────────────────────────────────────────────────────────────────────

def find_monthly_sheet(wb: openpyxl.Workbook) -> str:
    for name in wb.sheetnames:
        n = name.lower()
        if "monthly" in n and "price" in n:
            return name
    for name in wb.sheetnames:
        if "monthly" in name.lower():
            return name
    return wb.sheetnames[0]


def parse_pink_sheet(xlsx_bytes: bytes) -> list[dict]:
    wb = openpyxl.load_workbook(io.BytesIO(xlsx_bytes), data_only=True, read_only=True)
    sheet_name = find_monthly_sheet(wb)
    ws = wb[sheet_name]
    print(f"  Sheet: '{sheet_name}'")

    rows = list(ws.iter_rows(values_only=True))

    # Locate structure: find the first data row (col A parses as a date),
    # then the unit row is the last non-data row where col B contains "$",
    # and the header row is just above the unit row.
    unit_row_idx = None
    data_start_idx = None

    for i, row in enumerate(rows):
        # Check for unit row: col B (index 1) contains "$"
        if row[1] is not None and "$" in str(row[1]):
            unit_row_idx = i

        # Check for first data row
        if data_start_idx is None and unit_row_idx is not None:
            if parse_date_cell(row[0]) is not None:
                data_start_idx = i

        if data_start_idx is not None:
            break

    if unit_row_idx is None:
        # Fallback: scan any cell in each row for "$"
        for i, row in enumerate(rows):
            for cell in row[1:8]:
                if cell is not None and "$" in str(cell):
                    unit_row_idx = i
                    break
            if unit_row_idx is not None:
                break

    if unit_row_idx is None:
        raise ValueError("Cannot find unit row (row containing '$' in column B)")

    header_row_idx = unit_row_idx - 1

    if data_start_idx is None:
        for i in range(unit_row_idx + 1, len(rows)):
            if parse_date_cell(rows[i][0]) is not None:
                data_start_idx = i
                break

    if data_start_idx is None:
        raise ValueError("Cannot find first data row with a parseable date in column A")

    print(f"  Header row: {header_row_idx + 1}  |  Unit row: {unit_row_idx + 1}  |  Data from row: {data_start_idx + 1}")

    headers = rows[header_row_idx]
    units   = rows[unit_row_idx]

    # Build series list from column headers (skip col 0 = date column)
    series_list = []
    col_map = {}  # col_index -> position in series_list
    for col_i, (h, u) in enumerate(zip(headers, units)):
        if col_i == 0 or h is None:
            continue
        name = str(h).strip()
        if not name or name.lower() == "nan":
            continue
        unit = str(u).strip() if u is not None else ""
        series_list.append({
            "code":     make_code(name),
            "name":     name,
            "unit":     unit,
            "category": categorize(name),
            "data":     [],
        })
        col_map[col_i] = len(series_list) - 1

    print(f"  Commodity columns found: {len(series_list)}")

    # Parse data rows
    for row in rows[data_start_idx:]:
        date_str = parse_date_cell(row[0])
        if date_str is None:
            continue
        for col_i, series_idx in col_map.items():
            if col_i >= len(row):
                continue
            val = row[col_i]
            if val is None:
                continue
            try:
                fval = float(val)
                if fval > 0:
                    series_list[series_idx]["data"].append({
                        "date":  date_str,
                        "value": round(fval, 4),
                    })
            except (TypeError, ValueError):
                continue

    # Sort and drop empty series
    for s in series_list:
        s["data"].sort(key=lambda x: x["date"])
    series_list = [s for s in series_list if s["data"]]

    print(f"  Series with data: {len(series_list)}")
    return series_list


# ── YoY change ────────────────────────────────────────────────────────────────

def add_pct_year(series_list: list[dict]) -> None:
    for s in series_list:
        data = s["data"]
        by_date = {r["date"]: r["value"] for r in data}
        for row in data:
            yr, mo = row["date"].split("-")
            prev = f"{int(yr)-1}-{mo}"
            prev_val = by_date.get(prev)
            if prev_val and prev_val > 0:
                row["pct_year"] = round((row["value"] / prev_val - 1) * 100, 2)
            else:
                row["pct_year"] = None


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="World Bank Pink Sheet → pink_sheet.json")
    parser.add_argument("--url", default=PINK_SHEET_URL,
                        help="Pink Sheet Excel URL (update this if WB changes the document ID)")
    parser.add_argument("--out", default=None,
                        help="Output path (default: pink_sheet.json next to this script)")
    args = parser.parse_args()

    base_dir = os.path.dirname(os.path.abspath(__file__))
    out_path = args.out or os.path.join(base_dir, "pink_sheet.json")

    print("\nWorld Bank Pink Sheet Updater")
    print(f"  Source : {args.url}")
    print(f"  Output : {out_path}\n")

    print("Step 1 — Downloading Excel...")
    try:
        xlsx_bytes = download(args.url)
    except requests.HTTPError as e:
        print(f"\n  [ERROR] HTTP {e.response.status_code} — the World Bank URL may have changed.", file=sys.stderr)
        print("  Check https://www.worldbank.org/en/research/commodity-markets for the latest link.", file=sys.stderr)
        print("  Then rerun with:  python pink_sheet_update.py --url <new_url>", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"\n  [ERROR] {e}", file=sys.stderr)
        sys.exit(1)
    print(f"  Size: {len(xlsx_bytes) // 1024} KB")

    print("\nStep 2 — Parsing monthly prices sheet...")
    try:
        series_list = parse_pink_sheet(xlsx_bytes)
    except Exception as e:
        print(f"  [ERROR] {e}", file=sys.stderr)
        sys.exit(1)

    print("\nStep 3 — Computing year-on-year % changes...")
    add_pct_year(series_list)

    output = {
        "updated":    datetime.now().strftime("%Y-%m-%d"),
        "source":     "World Bank Commodity Price Data (Pink Sheet)",
        "source_url": args.url,
        "series":     series_list,
    }

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, separators=(",", ":"))

    total_pts = sum(len(s["data"]) for s in series_list)
    size_kb   = os.path.getsize(out_path) // 1024
    print(f"\n  Saved: {out_path}")
    print(f"  {len(series_list)} series  ·  {total_pts:,} data points  ·  {size_kb} KB")
    print(f"  Updated: {output['updated']}\n")


if __name__ == "__main__":
    main()
