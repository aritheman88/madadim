#!/usr/bin/env python3
"""
fetch_cbs_avg_prices.py
========================
Fetches CBS Table 5.6 -- "Average National Consumer Prices of Selected
Products and Services" (מחירים ממוצעים ארציים לצרכן על מוצרים ושירותים
נבחרים) -- and writes cbs_avg_prices.json for index.html (madadim.net).

Why this script exists: unlike every other series on the site, these are
RAW retail prices in current NIS (e.g. "olive oil, 750ml -> 37.68 ILS"),
not index numbers (base=100). CBS does NOT expose this table through
either public REST API (api.cbs.gov.il/index or apis.cbs.gov.il/series --
both were checked and neither recognizes these item codes, e.g. id=160095
returns "Error: Price Data" / "Result no found" respectively). The table
is published ONLY as a PDF/XLS attachment to the monthly CPI ("madad")
press release, at a URL of the form:

    https://www.cbs.gov.il/he/publications/madad/doclib/{year}/price{mm}a/a5_6_h.pdf

Each monthly PDF contains a rolling ~10-year trailing window per item, in
a fairly loose table layout (item code + name + pack size appear once per
item, at the top of that item's block; months run Jan..Dec per year-row).
Because the name text wraps across lines for longer product names, and a
handful of pages (mainly the fresh-produce and multi-cut/multi-brand combo
rows) pack multiple items' rows into a single extracted text line, this
parser's line-based approach can't cleanly recover every single item --
see the looks_clean() sanity filter below, which drops anything that still
looks contaminated (an embedded 6-digit code, a >6-word name, or a missing
pack size) rather than shipping a mangled name. That currently keeps
~65-70 of the ~90 items in the table; the dropped ones (mostly fresh
fruit/veg broken out by variety, e.g. tomatoes/apples/grapes by cultivar)
would need real bounding-box-aware table extraction to recover reliably.

Usage:
    pip install requests pdfplumber
    python3 fetch_cbs_avg_prices.py
    python3 fetch_cbs_avg_prices.py --year 2026 --month 6   # skip auto-discovery
"""
import argparse
import io
import json
import os
import re
import sys
from datetime import datetime, timezone, date

import requests

try:
    import pdfplumber
except ImportError:
    print("Missing dependency: pip install pdfplumber", file=sys.stderr)
    sys.exit(1)

BASE_URL = "https://www.cbs.gov.il/he/publications/madad/doclib/{year}/price{mm:02d}a/a5_6_h.pdf"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}

# ── Regexes used by the table-layout state machine ──────────────────────────
ROW_RE        = re.compile(r'^((?:[\d,]+\.\d{2}\s*){1,12})\s*(\d{4})\s*(.*)$')
SIZE_RE       = re.compile(r'^\d{1,3}(?:,\d{3})?$')       # 750 / 400 / 1,000
CODE_RE       = re.compile(r'^\d{6}$')                     # bare item-code line
ROMAN_RE      = re.compile(r'^(?:[IVX]+\s*)+$')             # "XII XI X ... I" month header
LEAD_SIZE_RE  = re.compile(r'^(\d{1,3}(?:,\d{3})?)\s+(.*\S)$')
DATERANGE_RE  = re.compile(r'^\d{4}\s+\S+\s*-\s*\d{4}\s+\S+$')  # "2015 June - 2006 January" banner
HEB_RE        = re.compile(r'[֐-׿]')

BOILERPLATE_MARKERS = [
    'לוח', 'קוד פריט', 'הופק לאחרונה', 'המחירים לצרכן .א',
    'ןוחרי רובע', 'בשקלים חדשים', 'חודש',
]

HEBREW_MONTHS = {
    'ינואר', 'פברואר', 'מרץ', 'אפריל', 'מאי', 'יוני',
    'יולי', 'אוגוסט', 'ספטמבר', 'אוקטובר', 'נובמבר', 'דצמבר',
}
# Some releases stamp a "current as of <month> <year>" freshness note that
# bleeds into the same extracted line as the following item's name (e.g.
# "2026 יולי שמן זית" instead of just "שמן זית"). Strip a leading
# "<4-digit year> <Hebrew month name>" prefix once it's been bidi-fixed.
LEAK_DATE_PREFIX_RE = re.compile(r'^\d{4}\s+(\S+)(\s+|$)')


def strip_date_leak(fixed_text):
    m = LEAK_DATE_PREFIX_RE.match(fixed_text)
    if m and m.group(1) in HEBREW_MONTHS:
        return fixed_text[m.end():]
    return fixed_text


def fix_bidi(line):
    """
    pdfplumber extracts PDF glyph streams in left-to-right visual order.
    For an RTL (Hebrew) run that's the MIRROR of logical reading order,
    while embedded LTR runs (numbers) are already correct -- so naive
    whole-line reversal would also flip digit order. This reverses only
    maximal whitespace-delimited runs of Hebrew tokens, leaving numeric/
    Latin tokens (values, years, item codes) in place.
    """
    tokens = line.split(' ')
    out, run = [], []

    def flush():
        if run:
            out.extend(reversed([t[::-1] for t in run]))
            run.clear()

    for tok in tokens:
        if HEB_RE.search(tok):
            run.append(tok)
        else:
            flush()
            out.append(tok)
    flush()
    return ' '.join(out)


def is_boilerplate(raw_line, fixed_line):
    if ROMAN_RE.match(raw_line) or DATERANGE_RE.match(raw_line) or DATERANGE_RE.match(fixed_line):
        return True
    return any(m in fixed_line or m in raw_line for m in BOILERPLATE_MARKERS)


def parse_table(pdf_bytes):
    """Returns {item_code: {code, name, size, unit, data: {YYYY-MM: value}}}."""
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        raw_lines = []
        for page in pdf.pages:
            text = page.extract_text() or ''
            raw_lines.extend(
                l.strip() for l in text.split('\n')
                if l.strip() and not l.startswith('- ')
            )

    items = {}
    cur_size = None
    cur_code = None
    name_buf = []

    def close_name(trailing_fixed):
        parts = [p for p in (name_buf + ([trailing_fixed] if trailing_fixed else [])) if p]
        full = ' '.join(parts).strip()
        # The "current as of <month> <year>" stamp can bleed in split across
        # two separate physical lines (e.g. name_buf=["2026"], next
        # line="יולי"), which per-line stripping can't catch -- so also
        # strip it once more from the fully joined name.
        full = strip_date_leak(full)
        words = full.split()
        unit = words[-1] if words else ''
        name = ' '.join(words[:-1])
        return name, unit

    for line in raw_lines:
        fx_probe = fix_bidi(line) if HEB_RE.search(line) else line
        if is_boilerplate(line, fx_probe):
            continue

        if CODE_RE.match(line):
            cur_code = line
            continue

        if SIZE_RE.match(line) and '.' not in line:
            cur_size = line.replace(',', '')
            continue

        m = ROW_RE.match(line)
        if not m:
            # Continuation line (wrapped product name); may itself start
            # with a bare pack-size token followed by more name text.
            lm = LEAD_SIZE_RE.match(line)
            if lm and not HEB_RE.search(lm.group(1)):
                cur_size = lm.group(1).replace(',', '')
                rest_txt = lm.group(2)
            else:
                rest_txt = line
            fx = strip_date_leak(fix_bidi(rest_txt))
            if HEB_RE.search(fx):
                name_buf.append(fx)
            continue

        vals_str, year, rest = m.groups()
        vals = [float(v.replace(',', '')) for v in vals_str.split()]
        rest = rest.strip()
        code_m = re.search(r'(\d{6})$', rest)
        row_code, trailing_fixed = None, ''
        if code_m:
            row_code = code_m.group(1)
            trailing_fixed = strip_date_leak(fix_bidi(rest[:code_m.start()].strip()))
        elif rest:
            trailing_fixed = strip_date_leak(fix_bidi(rest))

        if row_code:
            cur_code = row_code
            name, unit = close_name(trailing_fixed)
            name_buf = []
            if cur_code not in items:
                items[cur_code] = {'code': cur_code, 'name': name, 'size': cur_size, 'unit': unit, 'data': {}}
        elif trailing_fixed and HEB_RE.search(trailing_fixed) and cur_code not in items:
            # Item code appeared alone on a preceding line; this row carries the name tail.
            name, unit = close_name(trailing_fixed)
            name_buf = []
            items[cur_code] = {'code': cur_code, 'name': name, 'size': cur_size, 'unit': unit, 'data': {}}

        if cur_code is None or cur_code not in items:
            continue

        n = len(vals)
        for i, v in enumerate(vals):
            month = n - i  # values run XII..I (Dec..Jan) left to right
            items[cur_code]['data'][f'{year}-{month:02d}'] = v

    return items


def looks_clean(it):
    """Reject blocks contaminated by cross-item text bleed (see module docstring)."""
    name = it['name']
    if re.search(r'\d{6}', name):
        return False
    if len(name.split()) > 6:
        return False
    if not it['size']:
        return False
    if not it['data']:
        return False
    return True


def find_latest_pdf(session, year=None, month=None):
    """
    Return (bytes, year, month, url) for the most recent table 5.6 PDF.
    If year/month given, try only that. Otherwise walk backward from the
    current month for up to 14 months looking for the first hit (CBS
    publishes with roughly a 1-2 month lag, and doesn't backfill a file
    for months it hasn't released yet).
    """
    candidates = []
    if year and month:
        candidates.append((year, month))
    else:
        y, m = date.today().year, date.today().month
        for _ in range(14):
            candidates.append((y, m))
            m -= 1
            if m == 0:
                m, y = 12, y - 1

    for y, m in candidates:
        url = BASE_URL.format(year=y, mm=m)
        try:
            resp = session.get(url, headers=HEADERS, timeout=60)
        except requests.RequestException as e:
            print(f"  {y}-{m:02d}: request failed ({e}), trying next...", file=sys.stderr)
            continue
        if resp.status_code == 200 and len(resp.content) > 100_000:
            print(f"  Found: {y}-{m:02d} ({len(resp.content)//1024} KB)", file=sys.stderr)
            return resp.content, y, m, url
        print(f"  {y}-{m:02d}: not available (HTTP {resp.status_code}, {len(resp.content)} bytes)", file=sys.stderr)

    raise RuntimeError("Could not find a table 5.6 PDF in the last 14 months.")


def add_pct_year(sorted_dates, values):
    by_date = dict(zip(sorted_dates, values))
    out = {}
    for d, v in zip(sorted_dates, values):
        y, mo = d.split('-')
        prev_v = by_date.get(f"{int(y)-1}-{mo}")
        out[d] = round((v / prev_v - 1) * 100, 2) if prev_v else None
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--year', type=int, default=None)
    ap.add_argument('--month', type=int, default=None)
    ap.add_argument('--out', default=None)
    args = ap.parse_args()

    session = requests.Session()
    print("Locating most recent CBS table 5.6 (average consumer prices)...", file=sys.stderr)
    pdf_bytes, year, month, url = find_latest_pdf(session, args.year, args.month)

    print("Parsing PDF...", file=sys.stderr)
    items = parse_table(pdf_bytes)
    clean = {c: it for c, it in items.items() if looks_clean(it)}
    print(f"  Parsed {len(items)} blocks, {len(clean)} passed the clean-name filter.", file=sys.stderr)

    series_out = []
    for code, it in clean.items():
        dates = sorted(it['data'])
        values = [it['data'][d] for d in dates]
        pct_year_map = add_pct_year(dates, values)

        rows = []
        for i, d in enumerate(dates):
            v = values[i]
            prev_v = values[i - 1] if i > 0 else None
            pct = round((v / prev_v - 1) * 100, 2) if prev_v else None
            rows.append({"date": d, "value": v, "pct": pct, "pct_year": pct_year_map[d]})

        display_name = f"מחיר ממוצע {it['name']} ({it['size']} {it['unit']})"
        series_out.append({
            "code": f"CBSAVG_{code}",
            "name": display_name,
            "category": "מחירים ממוצעים ארציים לצרכן",
            "unit": "₪",
            "data": rows,
        })

    series_out.sort(key=lambda s: s["code"])

    last_date = max((r["date"] for s in series_out for r in s["data"]), default="unknown")
    out = {
        "updated":    datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "last_date":  last_date,
        "source":     "CBS Table 5.6 -- Average National Consumer Prices of Selected Products and Services",
        "source_url": url,
        "series":     series_out,
    }

    out_path = args.out or os.path.join(os.path.dirname(os.path.abspath(__file__)), "cbs_avg_prices.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))

    total_pts = sum(len(s["data"]) for s in series_out)
    print(f"\nWrote {out_path}", file=sys.stderr)
    print(f"  Series : {len(series_out)}", file=sys.stderr)
    print(f"  Points : {total_pts:,}", file=sys.stderr)
    print(f"  Through: {last_date}", file=sys.stderr)


if __name__ == "__main__":
    main()
