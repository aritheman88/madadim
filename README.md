# CBS Price Index Tracker

Tools for accessing and analyzing price index data from the **Israeli Central Bureau of Statistics (CBS)** API.

---

## Project Overview

The CBS publishes two main data systems accessible via API:

| System | Base URL | Contents |
|--------|----------|----------|
| **Time-Series DataBank** | `apis.cbs.gov.il/series` | Population, energy, health, employment, prices, trade, etc. — organized in 5 hierarchical levels |
| **Price Indices** | `api.cbs.gov.il/index` | ~1,681 price indices: CPI, housing, food, transport, etc. — organized by chapters → subjects → codes |

> **Important:** The CBS API only responds to **Israeli IP addresses**. Requests from abroad will be blocked or return no data.

---

## Scripts

### `cbs_catalog.py` — API Explorer & Catalog Downloader

Connects to both CBS API systems, prints a structured overview of all available databases, and saves the results to CSV files and a single Excel workbook.

**What it does:**

1. **Time-Series DataBank catalog** — fetches Level 1 (main subjects) and Level 2 (sub-topics) of the subject hierarchy
2. **Price Index catalog** — fetches chapter list and the full index tree (~1,681 index codes with metadata)
3. **Live CPI sample** — downloads the last 12 months of the Consumer Price Index (index code `120010`)
4. **Quick-reference card** — prints a cheat sheet of all key API endpoints

**Output files** (written to the script's directory):

```
cbs_timeseries_level1.csv    # Level-1 subject categories
cbs_timeseries_level2.csv    # Level-2 sub-topics with parent linkage
cbs_index_chapters.csv       # Price index chapters
cbs_index_tree.csv           # Full tree of all ~1,681 index codes
cbs_cpi_sample.csv           # CPI last 12 months (live)
cbs_catalog_all.xlsx         # All of the above in one workbook
```

**Usage:**

```bash
# Default (Hebrew labels)
python cbs_catalog.py

# English labels
python cbs_catalog.py --lang en

# Skip the live CPI API call
python cbs_catalog.py --no-sample
```

**Dependencies:**

```bash
pip install requests pandas tabulate openpyxl
```

---

### `transform_cbs_excel.py` — CBS Excel Reshaper

Transforms the wide-format Excel files downloaded from the CBS website into a clean long/pivot format suitable for analysis.

CBS Excel downloads typically have:
- Rows 1–12: metadata (index name, base period, units, etc.)
- Row 13: column headers (Hebrew month names across columns)
- Remaining rows: data, with combined `code - label` strings in the item column

**What it does:**

1. Separates metadata from data
2. Melts from wide (months as columns) to long format
3. Maps Hebrew month names to `MM` numeric format
4. Creates a `YYYY-MM` date column
5. Splits the `פריט` (item) column into numeric `קוד` (code) and `קטגוריה` (category label)
6. Pivots to a clean date-indexed table with a two-row MultiIndex header: category name over code
7. Saves reshaped data and original metadata as separate sheets

**Usage:**

```python
from transform_cbs_excel import transform_cbs_excel_with_split_headers

transform_cbs_excel_with_split_headers(
    input_file=r"path\to\cbs_download.xlsx",
    output_file=r"path\to\output.xlsx"
)
```

**Dependencies:**

```bash
pip install pandas openpyxl
```

---

## API Reference

### Price Indices (primary target for price tracking)

| Purpose | Example URL |
|---------|-------------|
| Chapter list | `https://api.cbs.gov.il/index/catalog/catalog?format=xml&lang=en` |
| Full index tree | `https://api.cbs.gov.il/index/catalog/tree?format=xml&lang=en` |
| Subjects in one chapter | `https://api.cbs.gov.il/index/catalog/chapter?id=a&format=xml&lang=en` |
| Index data by code | `https://api.cbs.gov.il/index/data/price?id=120010&format=xml&lang=en` |
| Index data with date range | `https://api.cbs.gov.il/index/data/price?id=120010&startPeriod=01-2020&endPeriod=12-2024&format=xml&lang=en` |
| Last N months | `https://api.cbs.gov.il/index/data/price?id=120010&last=12&format=xml&lang=en` |
| All indices (all bases) | `https://api.cbs.gov.il/index/data/price_all?format=xml&lang=en` |
| Linkage calculator | `https://api.cbs.gov.il/index/data/calculator/120010?value=100&date=01-01-2020&toDate=01-01-2024&format=xml&lang=en` |

### Time-Series DataBank

| Purpose | Example URL |
|---------|-------------|
| Level-1 subjects | `https://apis.cbs.gov.il/series/catalog/level?id=1&format=xml&lang=en` |
| Level-2 subjects | `https://apis.cbs.gov.il/series/catalog/level?id=2&subject=12&format=xml&lang=en` |
| Series data by code | `https://apis.cbs.gov.il/series/data/list?id=3763&format=xml&lang=en` |
| Series data by path | `https://apis.cbs.gov.il/series/data/path?id=2,1,1,2,379&format=xml&lang=en` |

### Key parameters

| Parameter | Values | Notes |
|-----------|--------|-------|
| `format` | `xml`, `json`, `csv`, `xls` | XML most reliable |
| `lang` | `en`, `he` | Label language |
| `last` | integer | Last N months of data |
| `startPeriod` / `endPeriod` | `MM-YYYY` | Date range filter |
| `pagesize` | up to `1000` | Paginate with `&page=2` |

---

## Key Price Index Codes

Some useful codes to get started (use `cbs_index_tree.csv` for the full list):

| Code | Description |
|------|-------------|
| `120010` | CPI — General index |
| `120020` | CPI — Food |
| `120030` | CPI — Housing |
| `120040` | CPI — Clothing & footwear |
| `120050` | CPI — Furniture & household equipment |
| `120060` | CPI — Health |
| `120070` | CPI — Transport |
| `120080` | CPI — Communications |
| `120090` | CPI — Education, culture & recreation |

Run `cbs_catalog.py` to get the full tree with all 1,681 codes saved to `cbs_index_tree.csv`.

---

## Notes & Gotchas

- **Israeli IP required** — use a VPN with an Israeli exit node if running from abroad (e.g., Mullvad with an Israeli server)
- **User-Agent required** — the API blocks simple/bot User-Agent strings; the scripts use a browser-like UA
- **`apis.cbs.gov.il`** (with `s`) = Time-Series DataBank
- **`api.cbs.gov.il`** (no `s`) = Price Indices — easy to mix up
- **XML is the most reliable format** — JSON and CSV endpoints occasionally return malformed responses
- **Pagination** — max `pagesize` is 1,000; use `&page=2`, `&page=3` for larger result sets
- **CBS Excel downloads** are wide-format with Hebrew headers and combined code-label strings — use `transform_cbs_excel.py` to normalize them

---

## Contact

CBS API support: info@cbs.gov.il