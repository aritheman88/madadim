"""
diagnose_fetch2.py
------------------
Tests the OVERLAPPING CHUNK approach for fixing the CBS base period problem.
Each chunk starts on the last month of the previous chunk (1 month overlap).
We use that overlap point to scale each new chunk to match the previous one.
"""

import requests
import xml.etree.ElementTree as ET
from datetime import date
from dateutil.relativedelta import relativedelta
import csv

CBS_API = "https://api.cbs.gov.il/index/data/price"

CBS_TRUE = {
    "2010-01": 101.8499, "2010-02": 101.4388, "2010-03": 101.7472, "2010-04": 101.6444,
    "2010-05": 101.0277, "2010-06": 100.9250, "2010-07": 100.5139, "2010-08": 98.7667,
    "2010-09": 98.3556,  "2010-10": 98.8695,  "2010-11": 97.3279,  "2010-12": 97.7390,
    "2011-01": 99.4,     "2011-06": 98.7,     "2011-12": 97.3,
    "2012-01": 97.2,     "2012-06": 95.6,     "2012-12": 94.8,
    "2013-01": 94.5646,  "2013-06": 94.7570,  "2013-12": 92.8330,
    "2014-01": 92.1596,  "2014-06": 93.0254,  "2014-12": 91.2938,
    "2015-01": 91.8935,  "2015-06": 92.0784,  "2015-12": 91.2464,
    "2016-01": 90.5992,  "2016-06": 91.9860,  "2016-12": 90.0445,
    "2017-01": 90.2424,  "2017-06": 90.3335,  "2017-12": 88.4193,
    "2018-01": 88.9662,  "2018-06": 88.6928,  "2018-12": 88.2370,
    "2019-01": 89.0415,  "2019-06": 88.6875,  "2019-12": 87.4483,
    "2020-01": 87.5368,  "2020-06": 87.8909,  "2020-12": 86.8288,
    "2021-01": 87.0095,  "2021-06": 90.4131,  "2021-12": 93.1185,
    "2022-01": 93.9912,  "2022-06": 93.9912,  "2022-12": 93.2058,
    "2023-01": 93.9978,  "2023-06": 91.5563,  "2023-12": 89.6783,
    "2024-01": 90.1478,  "2024-06": 90.2417,  "2024-12": 89.9600,
    "2025-01": 90.9544,  "2025-06": 89.9638,  "2026-04": 87.5324,
}


def cbs_period(ym):
    y, m = ym.split("-")
    return f"{m}-{y}"


def parse_xml_rows(xml_text):
    root = ET.fromstring(xml_text)
    rows = []
    for dm in root.iter("DateMonth"):
        year_el  = dm.find("year")
        month_el = dm.find("month")
        base_el  = dm.find("currBase")
        pct_y_el = dm.find("percentYear")

        if year_el is None or month_el is None or base_el is None:
            continue
        val_el = base_el.find("value")
        if val_el is None:
            continue
        try:
            val = float(val_el.text)
        except (ValueError, TypeError):
            continue

        y_str = year_el.text.strip()
        m_str = month_el.text.strip().zfill(2)
        pct = None
        if pct_y_el is not None and pct_y_el.text:
            try:
                pct = float(pct_y_el.text)
            except ValueError:
                pass

        rows.append({"date": f"{y_str}-{m_str}", "value": val, "pct_year": pct})

    last_page_el    = root.find(".//last_page")
    current_page_el = root.find(".//current_page")
    last_page    = int(last_page_el.text)    if last_page_el    is not None and last_page_el.text    else 1
    current_page = int(current_page_el.text) if current_page_el is not None and current_page_el.text else 1

    return rows, current_page, last_page


def fetch_chunk(code, from_ym, to_ym):
    base_url = (f"{CBS_API}?id={code}"
                f"&startPeriod={cbs_period(from_ym)}"
                f"&endPeriod={cbs_period(to_ym)}"
                f"&format=xml&lang=he&baseType=1")
    all_rows = []
    page = 1
    while True:
        url = base_url + (f"&page={page}" if page > 1 else "")
        resp = requests.get(url, headers={"Accept": "application/xml"}, timeout=30)
        resp.raise_for_status()
        rows, current_page, last_page = parse_xml_rows(resp.text)
        all_rows.extend(rows)
        if current_page >= last_page:
            break
        page += 1
    all_rows.sort(key=lambda r: r["date"])
    return all_rows


def run(code, from_ym, to_ym):
    print(f"\n{'='*60}")
    print(f"OVERLAPPING CHUNK TEST")
    print(f"Code: {code}  |  Range: {from_ym} -> {to_ym}")
    print(f"{'='*60}\n")

    CHUNK_MONTHS = 60

    all_chunks = []
    cursor = date.fromisoformat(from_ym + "-01")
    end    = date.fromisoformat(to_ym   + "-01")
    chunk_num = 0

    while True:
        chunk_end = cursor + relativedelta(months=CHUNK_MONTHS - 1)
        if chunk_end > end:
            chunk_end = end

        from_s = cursor.strftime("%Y-%m")
        to_s   = chunk_end.strftime("%Y-%m")

        print(f"Fetching Chunk {chunk_num+1}: {from_s} -> {to_s}")
        rows = fetch_chunk(code, from_s, to_s)
        print(f"  -> {len(rows)} rows  |  first={rows[0]['date']}={rows[0]['value']:.4f}  last={rows[-1]['date']}={rows[-1]['value']:.4f}")
        all_chunks.append(rows)
        chunk_num += 1

        if chunk_end >= end:
            break

        # next chunk starts at the last month of this chunk (overlap point)
        cursor = chunk_end

    # Chain chunks using the overlap point
    print(f"\n-- Chaining {len(all_chunks)} chunks via overlap --")

    result_by_date = {}
    for r in all_chunks[0]:
        result_by_date[r["date"]] = {"value": r["value"], "pct_year": r.get("pct_year")}

    for i in range(1, len(all_chunks)):
        chunk = all_chunks[i]
        overlap_date = chunk[0]["date"]

        if overlap_date not in result_by_date:
            print(f"  WARNING: overlap date {overlap_date} not found!")
            for r in chunk[1:]:
                if r["date"] not in result_by_date:
                    result_by_date[r["date"]] = {"value": r["value"], "pct_year": r.get("pct_year")}
            continue

        prev_value = result_by_date[overlap_date]["value"]
        curr_value = chunk[0]["value"]
        scale = prev_value / curr_value

        print(f"  Chunk {i+1}: overlap at {overlap_date}  prev={prev_value:.6f}  new={curr_value:.6f}  scale={scale:.8f}")

        for r in chunk[1:]:
            if r["date"] not in result_by_date:
                result_by_date[r["date"]] = {
                    "value":    r["value"] * scale,
                    "pct_year": r.get("pct_year"),
                }

    chained = [{"date": d, **v} for d, v in sorted(result_by_date.items())]

    # Rescale to 2010=100
    v2010 = [r["value"] for r in chained if r["date"].startswith("2010")]
    avg2010 = sum(v2010) / len(v2010) if v2010 else 1.0
    print(f"\n2010 average (pre-rescale): {avg2010:.6f}")
    for r in chained:
        r["value"] = round(r["value"] / avg2010 * 1000) / 10

    # Comparison table
    print(f"\n{'Date':<10}  {'Our Value':>10}  {'CBS True':>10}  {'Diff':>8}  {'Match?'}")
    print("-" * 55)
    has_errors = False
    for r in chained:
        our  = r["value"]
        true = CBS_TRUE.get(r["date"])
        if true is not None:
            diff  = our - true
            match = "OK" if abs(diff) < 0.15 else "WRONG"
            if match == "WRONG":
                has_errors = True
            print(f"{r['date']:<10}  {our:>10.2f}  {true:>10.4f}  {diff:>+8.4f}  {match}")

    if not has_errors:
        print("\n*** ALL VALUES MATCH CBS WEBSITE ***")
    else:
        print("\n*** SOME VALUES DO NOT MATCH ***")

    out_file = f"diagnose_overlap_{code}_{from_ym}_{to_ym}.csv"
    with open(out_file, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["date", "value_final", "pct_year"])
        writer.writeheader()
        for r in chained:
            writer.writerow({"date": r["date"], "value_final": r["value"], "pct_year": r.get("pct_year", "")})
    print(f"\nCSV saved: {out_file}")


if __name__ == "__main__":
    code_arg = input("Index code (e.g. 120670): ").strip()
    from_arg = input("From (e.g. 2010-01): ").strip() or "2010-01"
    to_arg   = input("To   (e.g. 2026-04): ").strip() or "2026-04"
    run(code_arg, from_arg, to_arg)