"""
Fetches three gas-price benchmarks and writes them to benchmarks.json
(annual, $/MMBtu) for the madadim.net/gas page:

  - Henry Hub  : EIA API v2 (needs the key in cbs/.env), already $/MMBtu
  - TTF        : Yahoo Finance "TTF=F" ticker (EUR/MWh) + Frankfurter EUR/USD
  - NBP        : investing.com monthly closes pasted below (GBp/therm) for
                 2011+, converted with Frankfurter GBP/USD; manually
                 digitized estimates for 2000-2010 (already $/MMBtu, from
                 an eyeballed chart -- treat as approximate, +/-0.3-0.5)

Re-run whenever you want fresher data. Network calls hit api.eia.gov,
query1.finance.yahoo.com, and api.frankfurter.app -- all free, no
Yahoo endpoint is unofficial/undocumented and could change without notice.
"""

import json
import re
import statistics
import urllib.request
import urllib.parse
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
env_path = r"C:\Users\Ariel\PycharmProjects\MyPythonScripts\cbs\.env"
output_path = r"C:\Users\Ariel\PycharmProjects\MyPythonScripts\cbs\gas\benchmarks.json"
# ---------------------------------------------------------------------------

MWH_TO_MMBTU = 3.412141633   # exact energy-unit conversion, not gas-specific
THERM_TO_MMBTU = 0.1         # 1 therm = 100,000 Btu = 0.1 MMBtu (exact)

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def http_get_json(url, headers=None):
    req = urllib.request.Request(url, headers=headers or HEADERS)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ── .env parsing (no external dependency) ───────────────────────────────────
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


# ── Frankfurter (ECB) exchange rates ────────────────────────────────────────
def annual_avg_rate(year, base, quote):
    """Average of daily {base}->{quote} rates for the given year."""
    today = datetime.now(timezone.utc).date()
    start = f"{year}-01-01"
    end_date = f"{year}-12-31"
    if year == today.year:
        end_date = today.isoformat()
    url = f"https://api.frankfurter.app/{start}..{end_date}?from={base}&to={quote}"
    data = http_get_json(url)
    rates = [v[quote] for v in data.get("rates", {}).values() if quote in v]
    if not rates:
        return None
    return statistics.mean(rates)


# ── Henry Hub via EIA (v1-series-id bridge on the v2 API) ──────────────────
def fetch_henry_hub(api_key):
    url = f"https://api.eia.gov/v2/seriesid/NG.RNGWHHD.A?api_key={api_key}"
    data = http_get_json(url)
    rows = data.get("response", {}).get("data", [])
    out = {}
    for row in rows:
        period = row.get("period")
        value = row.get("value")
        if period is None or value in (None, ""):
            continue
        year = int(str(period)[:4])
        try:
            out[year] = float(value)
        except (TypeError, ValueError):
            continue
    return out


# ── TTF via Yahoo Finance (unofficial) ──────────────────────────────────────
def fetch_ttf_eur_per_mwh():
    url = "https://query1.finance.yahoo.com/v8/finance/chart/TTF=F?range=15y&interval=1mo"
    data = http_get_json(url)
    result = data["chart"]["result"][0]
    timestamps = result["timestamp"]
    closes = result["indicators"]["quote"][0]["close"]

    by_year = {}
    for ts, close in zip(timestamps, closes):
        if close is None:
            continue
        year = datetime.fromtimestamp(ts, tz=timezone.utc).year
        by_year.setdefault(year, []).append(close)

    return {year: statistics.mean(vals) for year, vals in by_year.items()}


def convert_ttf_to_usd_mmbtu(eur_per_mwh_by_year):
    out = {}
    for year, eur_per_mwh in eur_per_mwh_by_year.items():
        rate = annual_avg_rate(year, "EUR", "USD")
        if rate is None:
            continue
        usd_per_mwh = eur_per_mwh * rate
        out[year] = usd_per_mwh / MWH_TO_MMBTU
    return out


# ── NBP: investing.com monthly closes (GBp/therm), pasted below ────────────
# Paste new rows here (same "Date\tPrice\t..." format) if you refresh this
# later -- only the first two columns (date, price) are used.
NBP_MONTHLY_RAW = """
Jul 01, 2026	105.80
Jun 01, 2026	87.26
May 01, 2026	90.54
Apr 01, 2026	93.50
Mar 01, 2026	104.16
Feb 01, 2026	68.76
Jan 01, 2026	69.15
Dec 01, 2025	65.13
Nov 01, 2025	70.71
Oct 01, 2025	77.97
Sep 01, 2025	80.23
Aug 01, 2025	80.71
Jul 01, 2025	89.46
Jun 01, 2025	84.88
May 01, 2025	84.23
Apr 01, 2025	80.68
Mar 01, 2025	89.96
Feb 01, 2025	91.64
Jan 01, 2025	103.09
Dec 01, 2024	98.12
Nov 01, 2024	112.67
Oct 01, 2024	99.85
Sep 01, 2024	95.33
Aug 01, 2024	103.28
Jul 01, 2024	98.34
Jun 01, 2024	95.99
May 01, 2024	95.66
Apr 01, 2024	86.77
Mar 01, 2024	80.41
Feb 01, 2024	75.29
Jan 01, 2024	84.94
Dec 01, 2023	90.49
Nov 01, 2023	107.98
Oct 01, 2023	127.47
Sep 01, 2023	123.15
Aug 01, 2023	135.26
Jul 01, 2023	131.41
Jun 01, 2023	134.63
May 01, 2023	113.94
Apr 01, 2023	146.30
Mar 01, 2023	147.12
Feb 01, 2023	138.05
Jan 01, 2023	165.32
Dec 01, 2022	196.46
Nov 01, 2022	359.66
Oct 01, 2022	310.52
Sep 01, 2022	454.78
Aug 01, 2022	488.20
Jul 01, 2022	353.88
Jun 01, 2022	264.65
May 01, 2022	203.57
Apr 01, 2022	189.02
Mar 01, 2022	183.93
Feb 01, 2022	157.26
Jan 01, 2022	137.55
Dec 01, 2021	102.32
Nov 01, 2021	143.07
Oct 01, 2021	116.75
Sep 01, 2021	149.77
Aug 01, 2021	87.78
Jul 01, 2021	71.42
Jun 01, 2021	66.25
May 01, 2021	54.55
Apr 01, 2021	52.94
Mar 01, 2021	46.78
Feb 01, 2021	43.36
Jan 01, 2021	44.17
Dec 01, 2020	43.44
Nov 01, 2020	38.26
Oct 01, 2020	36.76
Sep 01, 2020	37.29
Aug 01, 2020	37.99
Jul 01, 2020	32.14
Jun 01, 2020	34.37
May 01, 2020	32.17
Apr 01, 2020	31.70
Mar 01, 2020	31.88
Feb 01, 2020	34.90
Jan 01, 2020	34.88
Dec 01, 2019	41.62
Nov 01, 2019	40.97
Oct 01, 2019	42.79
Sep 01, 2019	48.39
Aug 01, 2019	46.47
Jul 01, 2019	50.39
Jun 01, 2019	48.48
May 01, 2019	49.05
Apr 01, 2019	50.73
Mar 01, 2019	47.75
Feb 01, 2019	52.56
Jan 01, 2019	54.28
Dec 01, 2018	53.95
Nov 01, 2018	60.18
Oct 01, 2018	62.10
Sep 01, 2018	67.65
Aug 01, 2018	63.45
Jul 01, 2018	56.33
Jun 01, 2018	55.74
May 01, 2018	53.98
Apr 01, 2018	50.91
Mar 01, 2018	46.06
Feb 01, 2018	44.07
Jan 01, 2018	44.64
Dec 01, 2017	47.00
Nov 01, 2017	48.62
Oct 01, 2017	46.15
Sep 01, 2017	44.39
Aug 01, 2017	44.45
Jul 01, 2017	42.78
Jun 01, 2017	42.00
May 01, 2017	42.95
Apr 01, 2017	42.58
Mar 01, 2017	43.32
Feb 01, 2017	44.49
Jan 01, 2017	46.48
Dec 01, 2016	48.75
Nov 01, 2016	45.12
Oct 01, 2016	47.46
Sep 01, 2016	41.20
Aug 01, 2016	39.10
Jul 01, 2016	42.04
Jun 01, 2016	41.69
May 01, 2016	36.37
Apr 01, 2016	34.93
Mar 01, 2016	32.17
Feb 01, 2016	32.52
Jan 01, 2016	33.91
Dec 01, 2015	33.79
Nov 01, 2015	37.22
Oct 01, 2015	38.02
Sep 01, 2015	40.76
Aug 01, 2015	42.30
Jul 01, 2015	43.62
Jun 01, 2015	45.98
May 01, 2015	46.35
Apr 01, 2015	47.22
Mar 01, 2015	47.78
Feb 01, 2015	50.19
Jan 01, 2015	46.57
Dec 01, 2014	50.17
Nov 01, 2014	55.81
Oct 01, 2014	53.75
Sep 01, 2014	57.21
Aug 01, 2014	59.09
Jul 01, 2014	57.77
Jun 01, 2014	57.28
May 01, 2014	59.64
Apr 01, 2014	60.46
Mar 01, 2014	60.32
Feb 01, 2014	62.46
Jan 01, 2014	63.62
Dec 01, 2013	66.42
Nov 01, 2013	67.36
Oct 01, 2013	66.53
Sep 01, 2013	65.90
Aug 01, 2013	67.84
Jul 01, 2013	68.52
Jun 01, 2013	67.80
May 01, 2013	67.64
Apr 01, 2013	67.09
Mar 01, 2013	69.17
Feb 01, 2013	69.16
Jan 01, 2013	67.56
Dec 01, 2012	65.95
Nov 01, 2012	65.19
Oct 01, 2012	64.67
Sep 01, 2012	63.65
Aug 01, 2012	64.71
Jul 01, 2012	62.06
Jun 01, 2012	62.00
May 01, 2012	61.96
Apr 01, 2012	66.49
Mar 01, 2012	69.20
Feb 01, 2012	68.03
Jan 01, 2012	65.52
Dec 01, 2011	63.58
Nov 01, 2011	60.83
Oct 01, 2011	65.32
Sep 01, 2011	68.35
Aug 01, 2011	71.97
Jul 01, 2011	69.86
""".strip()

# Manually digitized from a 2000-2019 benchmark comparison chart -- already
# in $/mmBtu. Approximate (eyeballed off gridlines, +/-0.3-0.5); replace with
# better sources if you find them. Only 2000-2010 is used (2011+ comes from
# the investing.com table above, which is the more reliable actual data).
NBP_MANUAL_2000_2010_USD_MMBTU = {
    2000: 2.7, 2001: 2.7, 2002: 2.2, 2003: 3.3, 2004: 4.4,
    2005: 7.5, 2006: 7.9, 2007: 6.0, 2008: 10.8, 2009: 4.8, 2010: 6.5,
}


def parse_nbp_monthly(raw):
    by_year = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = re.split(r"\t+", line)
        if len(parts) < 2:
            continue
        date_str, price_str = parts[0].strip(), parts[1].strip()
        try:
            year = datetime.strptime(date_str, "%b %d, %Y").year
            price = float(price_str.replace(",", ""))
        except ValueError:
            continue
        by_year.setdefault(year, []).append(price)
    return {year: statistics.mean(vals) for year, vals in by_year.items()}


def convert_nbp_to_usd_mmbtu(pence_per_therm_by_year):
    out = {}
    for year, pence in pence_per_therm_by_year.items():
        rate = annual_avg_rate(year, "GBP", "USD")
        if rate is None:
            continue
        # pence/therm -> GBP/mmBtu: x10 for therms-per-mmBtu, /100 for pence-to-pounds,
        # which nets out to a straight /10.
        gbp_per_mmbtu = pence / 10.0
        out[year] = gbp_per_mmbtu * rate
    return out


# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    result = {}

    print("Fetching Henry Hub from EIA...")
    try:
        eia_key = read_env_value(env_path, "eia_api")
        result["Henry Hub"] = fetch_henry_hub(eia_key)
        print(f"  got {len(result['Henry Hub'])} years")
    except Exception as e:
        print(f"  FAILED: {e}")
        result["Henry Hub"] = {}

    print("Fetching TTF from Yahoo Finance...")
    try:
        ttf_eur = fetch_ttf_eur_per_mwh()
        result["TTF"] = convert_ttf_to_usd_mmbtu(ttf_eur)
        print(f"  got {len(result['TTF'])} years")
    except Exception as e:
        print(f"  FAILED: {e}")
        result["TTF"] = {}

    print("Converting NBP (investing.com table + manual 2000-2010)...")
    try:
        nbp_pence = parse_nbp_monthly(NBP_MONTHLY_RAW)
        nbp_usd = convert_nbp_to_usd_mmbtu(nbp_pence)
        nbp_usd.update(NBP_MANUAL_2000_2010_USD_MMBTU)  # manual years win
        result["NBP"] = dict(sorted(nbp_usd.items()))
        print(f"  got {len(result['NBP'])} years")
    except Exception as e:
        print(f"  FAILED: {e}")
        result["NBP"] = {}

    output = {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "units": "$/MMBtu, annual average",
        "notes": {
            "Henry Hub": "EIA official annual spot price series (NG.RNGWHHD.A)",
            "TTF": "Yahoo Finance TTF=F (unofficial endpoint, EUR/MWh) converted via Frankfurter EUR/USD",
            "NBP": "2000-2010 digitized from a benchmark comparison chart (approximate); "
                   "2011+ from investing.com UK NBP futures monthly closes, converted via Frankfurter GBP/USD",
        },
        "benchmarks": result,
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\nWrote benchmarks to {output_path}")


if __name__ == "__main__":
    main()