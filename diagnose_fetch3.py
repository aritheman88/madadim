"""
diagnose_fetch3.py
------------------
Tests the PCT-CHAIN method for CBS index reconstruction.
Fetches full range as single request (paginated), then compounds
forward using the month-on-month percent field to avoid base period issues.
Max error ~0.4 index points over 16 years (from 1dp rounding in pct field).
"""

import requests
import xml.etree.ElementTree as ET
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


def fetch_all_pages(code, from_ym, to_ym):
    base_url = (f"{CBS_API}?id={code}"
                f"&startPeriod={cbs_period(from_ym)}"
                f"&endPeriod={cbs_period(to_ym)}"
                f"&format=xml&lang=he&baseType=1")
    all_rows = []
    page = 1
    while True:
        url = base_url + (f"&page={page}" if page > 1 else "")
        print(f"  GET page {page}...")
        resp = requests.get(url, headers={"Accept": "application/xml"}, timeout=30)
        resp.raise_for_status()
        root = ET.fromstring(resp.text)

        for dm in root.iter("DateMonth"):
            year_el  = dm.find("year")
            month_el = dm.find("month")
            base_el  = dm.find("currBase")
            pct_el   = dm.find("percent")
            pct_y_el = dm.find("percentYear")

            if year_el is None or month_el is None:
                continue

            val = None
            if base_el is not None:
                val_el = base_el.find("value")
                if val_el is not None:
                    try: val = float(val_el.text)
                    except: pass

            pct = None
            if pct_el is not None and pct_el.text:
                try: pct = float(pct_el.text)
                except: pass

            pct_year = None
            if pct_y_el is not None and pct_y_el.text:
                try: pct_year = float(pct_y_el.text)
                except: pass

            y_str = year_el.text.strip()
            m_str = month_el.text.strip().zfill(2)
            all_rows.append({"date": f"{y_str}-{m_str}", "value": val, "pct": pct, "pct_year": pct_year})

        last_page_el    = root.find(".//last_page")
        current_page_el = root.find(".//current_page")
        last_page    = int(last_page_el.text)    if last_page_el    is not None and last_page_el.text    else 1
        current_page = int(current_page_el.text) if current_page_el is not None and current_page_el.text else 1
        print(f"    -> page {current_page} of {last_page}, {len(all_rows)} rows so far")

        if current_page >= last_page:
            break
        page += 1

    seen, result = set(), []
    for r in sorted(all_rows, key=lambda x: x["date"]):
        if r["date"] not in seen:
            seen.add(r["date"])
            result.append(r)
    return result


def run(code, from_ym, to_ym):
    print(f"\n{'='*60}")
    print(f"PCT-CHAIN METHOD")
    print(f"Code: {code}  |  Range: {from_ym} -> {to_ym}")
    print(f"{'='*60}\n")

    rows = fetch_all_pages(code, from_ym, to_ym)
    print(f"\nTotal rows fetched: {len(rows)}")

    # Reconstruct via pct chain
    reconstructed = []
    current_val = rows[0]["value"]
    for i, r in enumerate(rows):
        if i == 0:
            reconstructed.append({"date": r["date"], "value": current_val, "pct_year": r["pct_year"]})
        else:
            if r["pct"] is not None:
                current_val = current_val * (1 + r["pct"] / 100)
            else:
                print(f"  WARNING: missing pct at {r['date']}, using raw value")
                current_val = r["value"]
            reconstructed.append({"date": r["date"], "value": current_val, "pct_year": r["pct_year"]})

    # Rescale to 2010=100
    v2010 = [r["value"] for r in reconstructed if r["date"].startswith("2010")]
    avg2010 = sum(v2010) / len(v2010) if v2010 else 1.0
    print(f"2010 average (pre-rescale): {avg2010:.6f}")
    for r in reconstructed:
        r["value"] = round(r["value"] / avg2010 * 1000) / 10

    # Comparison table (only months we have CBS_TRUE for)
    print(f"\n{'Date':<10}  {'Our Value':>10}  {'CBS True':>10}  {'Diff':>8}  {'Match?'}")
    print("-" * 55)
    has_errors = False
    for r in reconstructed:
        true = CBS_TRUE.get(r["date"])
        if true is not None:
            diff  = r["value"] - true
            match = "OK" if abs(diff) < 0.15 else "WRONG"
            if match == "WRONG":
                has_errors = True
            print(f"{r['date']:<10}  {r['value']:>10.2f}  {true:>10.4f}  {diff:>+8.4f}  {match}")

    if not has_errors:
        print("\n*** ALL VALUES MATCH CBS WEBSITE ***")
    else:
        max_diff = max(abs(r["value"] - CBS_TRUE[r["date"]]) for r in reconstructed if r["date"] in CBS_TRUE)
        print(f"\n*** MAX ERROR: {max_diff:.4f} index points ***")

    # Save CSV
    out_file = f"diagnose_pct_{code}_{from_ym}_{to_ym}.csv"
    with open(out_file, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["date", "value_final", "pct_year"])
        writer.writeheader()
        for r in reconstructed:
            writer.writerow({"date": r["date"], "value_final": r["value"], "pct_year": r.get("pct_year", "")})
    print(f"\nCSV saved: {out_file}")

    # Optional graph
    ans = input("\nExport graph? [Y/n]: ").strip().lower()
    if ans != "n":
        try:
            import matplotlib.pyplot as plt
            import matplotlib.dates as mdates
            from datetime import date as dt

            dates_all  = [dt.fromisoformat(r["date"] + "-01") for r in reconstructed]
            values_all = [r["value"] for r in reconstructed]

            dates_true  = [dt.fromisoformat(d + "-01") for d in CBS_TRUE if from_ym <= d <= to_ym]
            values_true = [CBS_TRUE[d] for d in CBS_TRUE if from_ym <= d <= to_ym]
            dates_true, values_true = zip(*sorted(zip(dates_true, values_true))) if dates_true else ([], [])

            fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 9), gridspec_kw={"height_ratios": [3, 1]})
            fig.suptitle(f"CBS Index {code}  |  {from_ym} → {to_ym}  |  PCT-chain method", fontsize=13)

            ax1.plot(dates_all, values_all, color="#4ade80", linewidth=1.5, label="PCT-chain (our value)")
            if dates_true:
                ax1.scatter(dates_true, values_true, color="#f59e0b", s=20, zorder=5, label="CBS website reference points")
            ax1.set_ylabel("Index value (2010=100)")
            ax1.legend()
            ax1.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
            ax1.xaxis.set_major_locator(mdates.YearLocator())
            ax1.grid(True, alpha=0.3)

            # Error panel: diff at reference points
            if dates_true:
                diffs = []
                true_by_date = {d: v for d, v in zip(dates_true, values_true)}
                our_by_date  = {dt.fromisoformat(r["date"] + "-01"): r["value"] for r in reconstructed}
                ref_dates = sorted(true_by_date.keys())
                ref_diffs = [our_by_date.get(d, float("nan")) - true_by_date[d] for d in ref_dates]
                colors = ["#f87171" if abs(d) >= 0.15 else "#4ade80" for d in ref_diffs]
                ax2.bar(ref_dates, ref_diffs, color=colors, width=20)
                ax2.axhline(0, color="white", linewidth=0.8)
                ax2.axhline(0.15, color="#f59e0b", linewidth=0.8, linestyle="--", label="±0.15 threshold")
                ax2.axhline(-0.15, color="#f59e0b", linewidth=0.8, linestyle="--")
                ax2.set_ylabel("Error vs CBS")
                ax2.legend(fontsize=8)
                ax2.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
                ax2.xaxis.set_major_locator(mdates.YearLocator())
                ax2.grid(True, alpha=0.3)

            fig.autofmt_xdate()
            plt.tight_layout()
            graph_file = f"diagnose_pct_{code}_{from_ym}_{to_ym}.png"
            plt.savefig(graph_file, dpi=150, bbox_inches="tight")
            print(f"Graph saved: {graph_file}")
            plt.show()

        except ImportError:
            print("matplotlib not installed. Run: pip install matplotlib")


if __name__ == "__main__":
    code_arg = input("Index code (e.g. 120670): ").strip()
    from_arg = input("From (e.g. 2010-01): ").strip() or "2010-01"
    to_arg   = input("To   (e.g. 2026-04): ").strip() or "2026-04"
    run(code_arg, from_arg, to_arg)