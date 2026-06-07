"""
cbs_catalog.py
==============
Connects to the Israeli Central Bureau of Statistics (CBS) API, prints
a structured overview of all available databases / series catalogs, and
saves every table to CSV files + a single Excel workbook.

  1. Time-Series DataBank  – Level 1 & 2 subject hierarchy
  2. Price Indices          – chapters + full index tree (1681 indices)
  3. Live CPI sample        – last 12 months of the Consumer Price Index

Output files (written to the same folder as this script):
    cbs_timeseries_level1.csv
    cbs_timeseries_level2.csv
    cbs_index_chapters.csv
    cbs_index_tree.csv
    cbs_cpi_sample.csv
    cbs_catalog_all.xlsx        ← all tables as separate sheets

Usage:
    python cbs_catalog.py              # prints + saves all files
    python cbs_catalog.py --lang he    # Hebrew labels (default: en)
    python cbs_catalog.py --no-sample  # skip the live CPI call

Requirements:
    pip install requests pandas tabulate openpyxl
    Must be run from an Israeli IP address.
"""

import argparse
import os
import sys
import time
import xml.etree.ElementTree as ET
from typing import Optional

import requests
import pandas as pd
from tabulate import tabulate

SERIES_BASE = "https://apis.cbs.gov.il/series"
INDEX_BASE  = "https://api.cbs.gov.il/index"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/xml, text/xml, */*",
}

# Output directory = same folder as this script
OUT_DIR = os.path.dirname(os.path.abspath(__file__))


def out(filename):
    return os.path.join(OUT_DIR, filename)


# ── HTTP ─────────────────────────────────────────────────────────────────────

def get_xml(url: str, params: dict, retries: int = 3) -> Optional[ET.Element]:
    p = {**params, "format": "xml", "download": "false"}
    for attempt in range(retries):
        try:
            r = requests.get(url, params=p, headers=HEADERS, timeout=20)
            r.raise_for_status()
            text = r.text.strip()
            if not text.startswith("<"):
                print(f"  [Not XML] {text[:100]}", file=sys.stderr)
                return None
            return ET.fromstring(text)
        except requests.exceptions.HTTPError as e:
            print(f"  [HTTP {r.status_code}] {url} — {e}", file=sys.stderr)
            return None
        except ET.ParseError as e:
            print(f"  [XML error] {e}", file=sys.stderr)
            return None
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
            else:
                print(f"  [Error] {url} — {e}", file=sys.stderr)
                return None


def txt(elem, tag):
    child = elem.find(tag)
    return (child.text or "").strip() if child is not None else ""


# ── Display helpers ──────────────────────────────────────────────────────────

def section(title):
    print()
    print("═" * 72)
    print(f"  {title}")
    print("═" * 72)


def subsection(title):
    print(f"\n{'─' * 60}")
    print(f"  {title}")
    print("─" * 60)


def save(df: pd.DataFrame, csv_name: str, label: str):
    path = out(csv_name)
    df.to_csv(path, index=False, encoding="utf-8-sig")
    print(f"  → Saved: {csv_name}  ({len(df)} rows)")
    return df


# ── 1. Time-Series DataBank ──────────────────────────────────────────────────

def fetch_series_levels(level_id: int, subject_code=None, lang: str = "he") -> list[dict]:
    params = {"lang": lang, "id": str(level_id), "pagesize": "1000"}
    if subject_code is not None:
        params["subject"] = str(subject_code)
    root = get_xml(f"{SERIES_BASE}/catalog/level", params)
    if root is None:
        return []
    rows = []
    for level in root.iter("Level"):
        path_elem = level.find("path")
        code = ""
        if path_elem is not None:
            int_elem = path_elem.find("int")
            if int_elem is not None:
                code = (int_elem.text or "").strip()
        name = txt(level, "name")
        rows.append({"Code": code, "Subject": name})
    return rows


def show_timeseries_catalog(lang: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    section("1. TIME-SERIES DATABANK  (apis.cbs.gov.il)")
    print("""
  The Time-Series DataBank holds statistical series covering:
  population, environment, energy, health, national expenditure,
  education, agriculture, prices, employment, foreign trade, etc.

  The catalog is organised in 5 hierarchical levels.
  Level 1 = main subjects  →  Level 5 = individual series groups.
""")

    subsection("Level 1 — Main Subjects")
    l1 = fetch_series_levels(1, None, lang)
    if not l1:
        print("  (no data — check Israeli IP)")
        return pd.DataFrame(), pd.DataFrame()

    df1 = pd.DataFrame(l1)
    print(tabulate(df1, headers="keys", tablefmt="rounded_outline", showindex=False))
    save(df1, "cbs_timeseries_level1.csv", "Level-1 subjects")

    subsection("Level 2 — Sub-Topics (per Level-1 subject)")
    all_l2 = []
    for row in l1:
        code = row["Code"]
        if not code:
            continue
        l2 = fetch_series_levels(2, code, lang)
        for r in l2:
            r["Parent Code"]    = code
            r["Parent Subject"] = row["Subject"]
            all_l2.append(r)
        time.sleep(0.15)

    df2 = pd.DataFrame()
    if all_l2:
        df2 = pd.DataFrame(all_l2)[["Parent Code", "Parent Subject", "Code", "Subject"]]
        print(tabulate(df2.head(80), headers="keys", tablefmt="rounded_outline", showindex=False))
        if len(df2) > 80:
            print(f"  … ({len(df2)} rows total — showing first 80)")
        save(df2, "cbs_timeseries_level2.csv", "Level-2 sub-topics")
    else:
        print("  (no Level-2 data returned)")

    print("""
  TIP — fetch actual series data:
    https://apis.cbs.gov.il/series/data/list?id=<SERIES_CODE>&format=xml&lang=en
""")
    return df1, df2


# ── 2. Price Indices ─────────────────────────────────────────────────────────

def show_index_catalog(lang: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    section("2. PRICE INDICES  (api.cbs.gov.il)")
    print("""
  Price indices track percentage change in expenditure for a fixed "basket"
  of goods and services.  Organised into chapters → subjects → index codes.
""")

    # ── 2a. Chapters ──
    subsection("2a — Index Chapters")
    root = get_xml(f"{INDEX_BASE}/catalog/catalog", {"lang": lang})
    df_ch = pd.DataFrame()
    if root is None:
        print("  (unavailable)")
    else:
        ch_rows = []
        for ch in root.iter("CatalogChapter"):
            ch_rows.append({
                "Chapter ID":   txt(ch, "chapterId"),
                "Name":         txt(ch, "chapterName"),
                "Order":        txt(ch, "chapterOrder"),
                "Main Code":    txt(ch, "mainCode"),
            })
        df_ch = pd.DataFrame(ch_rows)
        print(tabulate(df_ch, headers="keys", tablefmt="rounded_outline", showindex=False))
        save(df_ch, "cbs_index_chapters.csv", "Index chapters")

    # ── 2b. Full index tree ──
    subsection("2b — Full Index Tree (all 1681 index codes)")
    root = get_xml(f"{INDEX_BASE}/catalog/tree", {"lang": lang})
    df_tree = pd.DataFrame()
    if root is None:
        print("  (unavailable)")
        return df_ch, df_tree

    tree_rows = []
    for ch in root.iter("CatalogChapter"):
        chapter_id   = txt(ch, "chapterId")
        chapter_name = txt(ch, "chapterName")
        for subj in ch.iter("CatalogSubject"):
            subject_id   = txt(subj, "subjectId")
            subject_name = txt(subj, "subjectName")
            for code in subj.iter("CatalogCode"):
                tree_rows.append({
                    "Code":         txt(code, "codeId"),
                    "Name":         txt(code, "codeName"),
                    "Monthly":      txt(code, "isMonth"),
                    "From":         txt(code, "codeFromDate"),
                    "To":           txt(code, "codeToDate"),
                    "Subject":      subject_name,
                    "Subject ID":   subject_id,
                    "Chapter":      chapter_name,
                    "Chapter ID":   chapter_id,
                })

    df_tree = pd.DataFrame(tree_rows)
    print(f"  Total indices found: {len(df_tree)}")
    print(tabulate(df_tree[["Code","Name","Monthly","From","To","Chapter ID"]].head(40),
                   headers="keys", tablefmt="rounded_outline", showindex=False))
    if len(df_tree) > 40:
        print(f"  … ({len(df_tree)} rows total — showing first 40)")
    save(df_tree, "cbs_index_tree.csv", "Full index tree")

    return df_ch, df_tree


# ── 3. Live CPI data sample ──────────────────────────────────────────────────

def show_live_sample(lang: str) -> pd.DataFrame:
    section("3. LIVE DATA SAMPLE — CPI General (last 12 months)")
    root = get_xml(f"{INDEX_BASE}/data/price",
                   {"lang": lang, "id": "120010", "last": "12"})
    if root is None:
        print("  (unavailable)")
        return pd.DataFrame()

    rows = []
    for dm in root.iter("DateMonth"):
        year       = txt(dm, "year")
        month_desc = txt(dm, "monthDesc")
        pct        = txt(dm, "percent")
        pct_year   = txt(dm, "percentYear")
        base_elem  = dm.find("currBase")
        base_desc  = txt(base_elem, "baseDesc") if base_elem is not None else ""
        value      = txt(base_elem, "value")    if base_elem is not None else ""
        rows.append({
            "Period":      f"{month_desc} {year}",
            "Index Value": value,
            "Base":        base_desc,
            "Monthly %":   pct,
            "Annual %":    pct_year,
        })

    df = pd.DataFrame(rows)
    if not df.empty:
        print(tabulate(df, headers="keys", tablefmt="rounded_outline", showindex=False))
        save(df, "cbs_cpi_sample.csv", "CPI sample")
    else:
        print("  (no rows found)")
    return df


# ── 4. Quick-reference card ──────────────────────────────────────────────────

def show_reference():
    section("4. QUICK REFERENCE — ENDPOINT CHEAT SHEET")
    rows = [
        ("Time Series", "Level-1 subjects",
         "https://apis.cbs.gov.il/series/catalog/level?id=1&format=xml&lang=en"),
        ("Time Series", "Level-2 subjects (subject=<code>)",
         "https://apis.cbs.gov.il/series/catalog/level?id=2&subject=12&format=xml&lang=en"),
        ("Time Series", "Series data by code",
         "https://apis.cbs.gov.il/series/data/list?id=3763&format=xml&lang=en"),
        ("Time Series", "Series data by path",
         "https://apis.cbs.gov.il/series/data/path?id=2,1,1,2,379&format=xml&lang=en"),
        ("Price Indices", "Chapter list",
         "https://api.cbs.gov.il/index/catalog/catalog?format=xml&lang=en"),
        ("Price Indices", "Full index tree (all codes)",
         "https://api.cbs.gov.il/index/catalog/tree?format=xml&lang=en"),
        ("Price Indices", "Subjects in chapter (e.g. a=CPI)",
         "https://api.cbs.gov.il/index/catalog/chapter?id=a&format=xml&lang=en"),
        ("Price Indices", "Index data by code",
         "https://api.cbs.gov.il/index/data/price?id=120010&format=xml&lang=en"),
        ("Price Indices", "Index data with date range",
         "https://api.cbs.gov.il/index/data/price?id=120010&startPeriod=01-2020&endPeriod=12-2024&format=xml&lang=en"),
        ("Price Indices", "All indices (all bases)",
         "https://api.cbs.gov.il/index/data/price_all?format=xml&lang=en"),
        ("Price Indices", "Linkage calculator",
         "https://api.cbs.gov.il/index/data/calculator/120010?value=100&date=01-01-2020&toDate=01-01-2024&format=xml&lang=en"),
    ]
    df = pd.DataFrame(rows, columns=["System", "Purpose", "Example URL"])
    print(tabulate(df, headers="keys", tablefmt="rounded_outline", showindex=False,
                   maxcolwidths=[15, 35, 75]))
    print("""
  NOTES:
  • CBS API only responds to Israeli IP addresses.
  • User-Agent must be a browser-like string — simple bot strings are blocked.
  • apis.cbs.gov.il  (with 's') = Time-Series DataBank
  • api.cbs.gov.il   (no 's')   = Price Indices
  • Supported formats: xml | json | csv | xls
  • Max pagesize: 1,000  (paginate with &page=2, &page=3, …)
  • Questions: info@cbs.gov.il
""")
    return df


# ── Excel workbook ───────────────────────────────────────────────────────────

def save_excel(tables: dict[str, pd.DataFrame]):
    """Write all tables into a single Excel workbook, one sheet each."""
    path = out("cbs_catalog_all.xlsx")
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for sheet_name, df in tables.items():
            if df is None or df.empty:
                continue
            # Excel sheet names max 31 chars
            df.to_excel(writer, sheet_name=sheet_name[:31], index=False)
            # Auto-fit column widths
            ws = writer.sheets[sheet_name[:31]]
            for col in ws.columns:
                max_len = max(
                    (len(str(cell.value)) for cell in col if cell.value),
                    default=10
                )
                ws.column_dimensions[col[0].column_letter].width = min(max_len + 2, 60)
    print(f"\n  → Saved Excel workbook: cbs_catalog_all.xlsx  ({len(tables)} sheets)")


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="CBS (Israel) API Catalog Explorer")
    parser.add_argument("--lang",      default="he", choices=["en", "he"],
                        help="Label language (default: he)")
    parser.add_argument("--no-sample", action="store_true",
                        help="Skip the live CPI data sample")
    args = parser.parse_args()

    print()
    print("╔══════════════════════════════════════════════════════════════════════╗")
    print("║   Israeli Central Bureau of Statistics (CBS) — API Catalog Explorer ║")
    print("╚══════════════════════════════════════════════════════════════════════╝")
    print(f"  Language: {args.lang.upper()}   |   Output: CSV files + Excel workbook")
    print(f"  Saving to: {OUT_DIR}")

    df_l1, df_l2     = show_timeseries_catalog(args.lang)
    df_ch, df_tree   = show_index_catalog(args.lang)
    df_cpi           = show_live_sample(args.lang) if not args.no_sample else pd.DataFrame()
    df_ref           = show_reference()

    # Write everything into one Excel file
    section("5. SAVING EXCEL WORKBOOK")
    save_excel({
        "TimeSeries_Level1":  df_l1,
        "TimeSeries_Level2":  df_l2,
        "PriceIndex_Chapters": df_ch,
        "PriceIndex_Tree":    df_tree,
        "CPI_Last12Months":   df_cpi,
        "API_Reference":      df_ref,
    })

    print()
    print("  Done. Files written:")
    for f in ["cbs_timeseries_level1.csv", "cbs_timeseries_level2.csv",
              "cbs_index_chapters.csv", "cbs_index_tree.csv",
              "cbs_cpi_sample.csv", "cbs_catalog_all.xlsx"]:
        print(f"    {os.path.join(OUT_DIR, f)}")
    print()


if __name__ == "__main__":
    main()