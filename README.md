# madadim.net

Source for **madadim.net** — a price-index / inflation explorer that lets you search, chart,
and export Israeli CBS price indices alongside a growing set of international price data
(commodity futures, USDA dairy, US CPI food items, Eurostat, World Bank, Bank of Israel FX),
plus a standalone world-energy explorer at `/worldenergy` (Our World in Data).
Deployed via GitHub Pages (`CNAME` = `madadim.net`); pushing to `main` deploys directly, no
build step.

This file is the map of the whole repo: what each script does, how the site loads data, what's
already been tried and rejected, and how to extend it. It's written so a fresh session (human or
Claude) can pick this up without re-deriving context that took real effort to figure out the
first time.

---

## How the site is built

`index.html` is a single static page (RTL, Hebrew-first UI) that does two different things
depending on the data source:

1. **CBS Price Indices — fetched live, client-side.** `fetchIndex()` in `index.html` calls
   `api.cbs.gov.il` directly from the visitor's browser for whichever of the ~1,681 CBS series
   they pick, for whatever date range they choose. This is the *only* source handled this way.
2. **Everything else — pre-fetched to a static JSON file, checked into the repo.** A Python
   `fetch_*.py` script hits the real source, cleans/reshapes the data, and writes a JSON file
   (`pink_sheet.json`, `futures.json`, `usda_dairy.json`, `bls_food_cpi.json`,
   `eurostat_*.json`, `boi_rates.json`, `cbs_avg_prices.json`). `index.html` has a matching
   `loadX()` function that just does `fetch('thatfile.json')` on page load and pushes entries
   into `CATALOG`.

**Why the split:** CBS's API happens to allow direct cross-origin browser calls and covers far
too many series/date-ranges to pre-bundle. Nothing else does both of those things — some sources
don't send CORS headers at all (confirmed by checking response headers directly, e.g. Yahoo
Finance sends none), some return formats a browser can't parse cheaply (Eurostat's live JSON-stat
API returns an async-job error for the queries this site needs; World Bank ships an Excel file),
and some need real data cleaning (USDA's raw rows need grouping into series; BLS needs de-duping
and a monthly-only filter). So: fetch once in Python, ship the clean JSON, load it directly in
the browser.

Each source's `CATALOG` entries get a `source` tag (`'wb'`, `'boi'`, `'eurostat'`, `'avgprice'`,
`'futures'`, `'usda'`, `'bls'`) which drives a colored badge in the search results, and codes are
prefixed to route them away from the live-CBS-fetch path (see `fetchSelected()` — it skips
`WB_`, `BOI_`, `EUROSTAT_`, `CBSAVG_`, `FUT_`, `USDA_`, `BLS_` prefixes since those are
pre-loaded, not fetched on demand).

**Data model:** everything is monthly (`YYYY-MM`), including sources whose native data is daily
or weekly — futures and USDA are aggregated to a monthly average in the fetch script to match
CBS/Pink Sheet/Eurostat's own convention. Every series carries a `pct_year` field (value vs. the
same month a year earlier) computed at fetch time.

**Site UI internals worth knowing:**
- A disclaimer overlay and a **client-side-only** password gate (`checkPassword()` in
  `index.html`) sit in front of the page. The password is hardcoded in plaintext in the tracked,
  public `index.html` — it is not real security, just a soft "not ready for randoms yet" gate.
  Don't mistake it for an auth boundary.
- Search matches word-boundary substrings (`matchesWord()`) against a series' `name` or
  `subject`, or plain substring against `code`. This is why, e.g., every BLS series has `US
  CPI-U Index` in its `subject` and `(US CPI)` in its `name` — typing "CPI" needs to reliably
  surface all of them, and word-boundary matching is case-insensitive but does require the exact
  token to appear somewhere.
- Max 10 series selected at once (`selectedMap` cap in `toggleIndex()`).
- XLSX export (`⬇ XLSX` button) works off whatever's currently loaded in `selectedMap`.

---

## Data sources

| Source | Fetch script | Output | Code prefix | History | Needs |
|---|---|---|---|---|---|
| CBS Price Indices | *(live, in `index.html`)* | — | *(raw CBS codes)* | full | Israeli IP |
| CBS avg consumer prices | `fetch_cbs_avg_prices.py` | `cbs_avg_prices.json` | `CBSAVG_` | recent | Israeli IP, PDF parsing |
| World Bank Pink Sheet | `pink_sheet_update.py` | `pink_sheet.json` | `WB_` | to 1960 | — |
| Yahoo Finance futures | `fetch_yahoo_futures.py` | `futures.json` | `FUT_` | varies by contract | — |
| USDA MARS dairy | `fetch_usda_dairy.py` | `usda_dairy.json` | `USDA_` | to 1993 | `usda_key` |
| BLS CPI-U food items | `fetch_bls_food_cpi.py` | `bls_food_cpi.json` | `BLS_` | to 1913 (aggregates) | `bls_key` |
| Bank of Israel FX | `boi_update.py` | `boi_rates.json` | `BOI_` | to 1948 | — |
| Eurostat HICP | `fetch_eurostat_hicp.py` | `eurostat_hicp.json` | `EUROSTAT_CP...` | full | — |
| Eurostat HICP (live/minr) | `fetch_eurostat_hicp_live.py` | `eurostat_hicp_live.json` | `EUROSTAT_LIVECP...` | full | — |
| Eurostat agri producer prices | `fetch_eurostat_agri.py` | `eurostat_agri.json` | `EUROSTAT_AGRI...` | full | — |

### CBS Price Indices (live)
The Israeli Central Bureau of Statistics publishes two API systems:

| System | Base URL | Contents |
|---|---|---|
| Time-Series DataBank | `apis.cbs.gov.il/series` | Population, energy, health, employment, prices, trade — 5 hierarchical levels |
| Price Indices | `api.cbs.gov.il/index` | ~1,681 price indices: CPI, housing, food, transport, etc. |

**The CBS API only responds to Israeli IP addresses** — requests from abroad are blocked or
return nothing. It also blocks simple/bot User-Agent strings; `index.html`'s `fetchIndex()` and
the standalone scripts (`cbs catalog.py`, `fetch_cbs_avg_prices.py`) all send a browser-like UA.
XML is the most reliable format; JSON/CSV occasionally come back malformed. Key endpoints:

```
Chapter list       https://api.cbs.gov.il/index/catalog/catalog?format=xml&lang=en
Full index tree    https://api.cbs.gov.il/index/catalog/tree?format=xml&lang=en
Index data         https://api.cbs.gov.il/index/data/price?id=120010&format=xml&lang=en
Date range         &startPeriod=01-2020&endPeriod=12-2024
Last N months       &last=12
```
CBS also periodically revises the official base period for a series — `fetchIndex()` rebuilds a
continuously-chained series from the month-over-month `%` figures rather than trusting the raw
absolute values across the whole range, since a rebase can land mid-range and would otherwise
show as a spurious jump.

### World Bank Pink Sheet
Monthly *physical/spot* commodity prices (actual trade transactions, not futures), auto-discovers
the current XLS URL from the World Bank's commodity-markets page so it survives them rotating
document IDs. ~70 series across Energy/Metals/Agriculture/Fertilizers.

### Yahoo Finance futures
Front-month **continuous futures** settlement prices (`CL=F`, `GC=F`, `ZW=F`, etc.), not physical
prices — this measures what the market is paying today for the *nearest* delivery month, rolled
forward automatically as each contract expires, not a fixed 6- or 12-month-out forecast. 29
symbols: energy, precious/industrial metals, grains/oilseeds, softs, livestock/dairy. Fetched via
Yahoo's undocumented `query1.finance.yahoo.com/v8/finance/chart/{symbol}` endpoint with
`interval=1mo` (Yahoo aggregates to monthly bars server-side, no client-side aggregation needed).
No API key, but no CORS headers either (confirmed directly) — hence pre-fetch, not live.

**Investigated and rejected: investing.com.** No public API, and its ToS explicitly forbids
automated extraction/reproduction of its data — the most popular scraper library for it
(`investpy`) was shut down after a cease-and-desist over exactly this. Don't scrape it directly;
Yahoo's chart endpoint gives materially the same data legitimately.

**Investigated and rejected: Canola (`RS=F`).** Resolves as HTTP 200 but is an inert
`ALTSYMBOL` placeholder with no real data feed on Yahoo — dropped from `SYMBOLS`.

### USDA MARS dairy
**Only the "Point of Sale - Dairy" report family returns structured numeric prices** through the
public MARS API (`marsapi.ams.usda.gov/services/v1.2/reports/{slug}`, HTTP Basic auth with the
API key as username and blank password). This was checked directly, not assumed: egg/poultry/
cotton/grain reports (e.g. "Daily National Shell Egg Index", "Weekly National Chicken Report")
return `results` rows with every price field `null` — the numbers only exist as free-form prose
in a `report_narrative` string (checked the actual narrative text; it's pure qualitative language
like "steady to higher," zero digits). Getting those would need fragile text-scraping, not
implemented.

Two data-quality issues found and handled generically (not as one-off patches) in
`fetch_usda_dairy.py`:
- **Basis-pricing rows.** All three US-domestic butter reports (Central/East/West) report a
  `price_Unit` of `"Basis Pricing"` — a cents-vs-CME differential, not an absolute price. The
  script skips any row whose unit contains "basis", generically, not just for butter.
- **Stray region mislabeling.** One historical row in 30+ years of "Butter/Butteroil - Europe"
  data says region `"Europe"` instead of `"West Europe"` — a one-off data entry glitch, not a
  real regional split. The script merges any region variant with <10 rows into the dominant
  region for that commodity, so a lone mislabeled row doesn't fork off a phantom near-empty
  series.

12 series across Europe/Oceania/US regions, built from the actual `(commodity, region)`
combinations found in each report's data — not assumed 1:1 with slugs, since e.g. slug `1098`
("Butter/Butteroil - Europe") contains two distinct commodities in one report.

Exploratory scripts from before this was wired into the main site live in `usda/`
(`browser_usda.py` to search the master report catalog by keyword, `usda_sample.py` — the
original single-slug proof of concept). `usda/config.py` reads `usda_key` from the repo-root
`.env`.

### BLS CPI-U food items
U.S. retail consumer price *indices* (not wholesale/futures) for specific food items — same
underlying government data that paid resellers like economy.com repackage and charge for. Pulled
directly from BLS's free, documented Public Data API (`api.bls.gov/publicAPI/v2/timeseries/data/`).

Series IDs are `CUUR0000` + an item code (`CU` = CPI-U survey, `U` = not seasonally adjusted,
`0000` = U.S. city average). **Item codes came from BLS's own published catalog**
(`https://download.bls.gov/pub/time.series/cu/cu.item`), not memory — this caught a real mistake
mid-session: `SEFP01` was initially assumed to be Butter, but the authoritative catalog shows
it's actually **Coffee** (`SEFS01` is Butter and margarine). Always verify against `cu.item`
before adding a new series ID.

23 series: 2 aggregates (Food, Food at home) + cereals/bakery, meat/poultry/fish/eggs, dairy,
produce, and beverages/sweets/fats. Several use a more granular "item stratum" code (`SS`
prefix) one level below the commonly-cited aggregate, e.g. standalone Rice (`SS01031`) alongside
the aggregate "Rice, pasta, cornmeal" (`SEFA03`), or standalone Butter (`SS10011`) alongside the
blended "Butter and margarine" (`SEFS01`).

**Not trackable, checked directly:** tea and chocolate/cocoa have no dedicated CPI item code at
all (tea only exists folded into "other beverage materials"). Soybeans and corn aren't CPI items
*at all* — they're raw agricultural commodities, not retail grocery purchases, so BLS doesn't
index them. Both are already covered elsewhere on the site (Yahoo futures `ZS=F`/`ZC=F`, World
Bank Pink Sheet).

### Bank of Israel FX / Eurostat HICP & agri producer prices
Straightforward monthly pulls from each institution's own official API (`edge.boi.org.il`,
`ec.europa.eu/eurostat/api/dissemination`). Eurostat's *live* JSON-stat API returns an
`ASYNCHRONOUS_RESPONSE` (HTTP 413) error for the queries this site needs even filtered to one
series — that's why `fetch_eurostat_hicp.py`/`_agri.py` use Eurostat's bulk TSV download instead
and ship a pre-fetched JSON, same as everything else non-CBS.

---

## Secrets (`.env`)

Repo-root `.env` (gitignored, never commit it) holds:
```
eia_api = ...      # EIA API key (used by "benchmark exports.py")
usda_key = ...      # USDA MARS API key
bls_key = ...      # BLS Public Data API key
```
Scripts read it with a small hand-rolled parser (`read_env_value()`, duplicated per-script rather
than adding a `python-dotenv` dependency — see `fetch_yahoo_futures.py` or `fetch_usda_dairy.py`
for the pattern).

**Incident, fixed:** `usda_key` was originally hardcoded in plaintext in `usda/config.py`, which
is a *tracked* file (unlike `.env`) — it was committed and pushed to this public GitHub repo. Key
was rotated by the repo owner; `usda/config.py` now reads from `.env` like everything else. If
you ever see a literal secret value in a diff about to be committed, stop and flag it before
committing — check `git status`/the diff, not just intent.

To get your own keys: USDA MARS at mymarketnews.ams.usda.gov (free), BLS at
data.bls.gov/registrationEngine (free, raises the rate limit from 25 to 500 series/day).

---

## Refreshing all data

No CI/cron is set up in this repo — refresh is manual. Python isn't necessarily on `PATH`; this
session used a full path to an Anaconda install
(`C:/Users/Ariel/anaconda3/python.exe`) since a Windows Store Python shim was shadowing it. Check
`python --version` actually runs before assuming `python script.py` will work.

```bash
python boi_update.py
python pink_sheet_update.py
python fetch_eurostat_agri.py
python fetch_eurostat_hicp.py
python fetch_eurostat_hicp_live.py
python fetch_cbs_avg_prices.py      # needs Israeli IP
python fetch_yahoo_futures.py
python fetch_usda_dairy.py          # needs usda_key in .env
python fetch_bls_food_cpi.py        # needs bls_key in .env
python worldenergy/owid_energy_export.py   # standalone page, not the main CATALOG
```
Dependencies across all scripts: `requests`, `openpyxl`, `beautifulsoup4` (`pip install requests
openpyxl beautifulsoup4`). No `requirements.txt` exists yet.

Then `git add` the changed `*.json` files (and any script changes), commit, and `git push origin
main` — GitHub Pages deploys straight from `main`, no build step.

---

## Adding a new data source

The pattern used for futures/USDA/BLS, in order:

1. **Check the source is legitimate to pull from.** Official API > well-known free endpoint used
   by other open-source tools (like Yahoo's chart API, via `yfinance`) > nothing. Don't scrape a
   site whose ToS forbids it (see investing.com above) — find what the *primary* source is
   instead; a paid reseller is usually just repackaging free government/institutional data (see
   BLS vs. economy.com).
2. **Verify the actual schema by calling the API, not by assuming from docs/memory.** Field names,
   which report types return structured data vs. narrative text, unit inconsistencies (see USDA's
   basis-pricing gotcha) — all found by making real requests and inspecting the response, not
   guessed.
3. **Curate the series list with the user** — don't dump an entire catalog. Ask about scope
   (which categories, how granular) before writing the fetch script.
4. **Write `fetch_X.py`**: mirror the existing scripts' shape — a `SOURCE`/`ITEMS` config list up
   top, a fetch function, monthly aggregation if the source isn't already monthly, a
   `add_pct_year()` pass, then write `{updated, last_date, source, source_url, series: [...]}` to
   a JSON file next to the script. Each series needs `code` (prefixed uniquely), `name`, `unit`,
   `category`, `data: [{date: "YYYY-MM", value, pct_year}]`.
5. **Wire into `index.html`**: add a `loadX()` function (copy `loadFutures()` or `loadUsdaDairy()`
   as a template), a colored badge in the `srcBadge` ternary chain, add the new code prefix to
   *both* prefix-exclusion checks in `fetchSelected()`, and call `loadX()` alongside the other
   init calls at the bottom of the script.
6. **Test in an actual browser** before committing — `python -m http.server` in the repo dir,
   `Lamas`-gate + disclaimer dismiss (or just clear `.overlay` elements via JS console), search
   for a series, select it, confirm the chart actually renders with sane-looking data (a real
   historical event, like the 2022 wheat/butter price spike, is a good sanity check that values
   aren't garbled).
7. **Commit and push only when asked** — this repo's owner reviews before every push.

---

## Other pages in this repo

- `change/` — "CBS // Price Change Calculator", linked from the main page.
- `gas/` — "Natural Gas // Imports & Price Explorer" (`gas_export.py` builds `gas_data.json`).
- `worldenergy/` — "WorldEnergy // OWID Energy Explorer" (see below).
- `lobbyists/` — "Lobbyists // Network Explorer" (Israeli lobbyist registry visualization).

`change/` and `lobbyists/` weren't touched by the data-source work described above.

### worldenergy/ — Our World in Data energy dataset

Standalone page at `madadim.net/worldenergy` (linked from the main header and the
gas page). Same shell as `gas/`: dark UI, `Lamas` password gate, Chart.js,
SheetJS export. It does **not** feed the main `index.html` CATALOG — the OWID
data is entity×year panel data (220 countries + 16 aggregates × 66 metrics ×
1965–present), which doesn't fit the flat named-series model.

| | |
|---|---|
| Fetch script | `worldenergy/owid_energy_export.py` (no API key; `pip install requests`) |
| Output | `worldenergy/energy_data.json` (~3.8 MB; GitHub Pages gzips it) |
| Upstream | `https://raw.githubusercontent.com/owid/energy-data/master/owid-energy-data.csv` + its codebook |
| Licence | OWID publishes the dataset under CC-BY for any reuse (see the energy-data repo README) |

**Three view modes** in `worldenergy/index.html`:
1. **Energy mix** — stacked area, primary-energy consumption by source for one
   entity, TWh or % toggle.
2. **Electricity mix** — same for electricity generation by source.
3. **Compare** — one metric, up to 8 entities, multi-line.

**Data-coverage gotchas found while building it (all handled in-script or in the UI):**
- The CSV carries ~90 no-ISO "entities" that are mostly source-internal regions
  (`Africa (EI)`, `OPEC (EIA)`, `Europe (Shift)`, …) which overlap and
  double-count. The script keeps only OWID's own canonical aggregates (World,
  6 continents, EU-27, 4 income groups) plus OECD/G7/G20/OPEC each taken from a
  **single** source lineage so one entity is never a methodology mix. All other
  no-ISO entities are dropped (78 of them, printed at the end of the run).
- The Energy-Institute fuel-by-fuel breakdown only covers ~80 countries; Ember's
  electricity generation covers ~215. OWID still emits zero-filled
  `nuclear_consumption` / `biofuel_consumption` for countries with neither, so a
  naive energy-mix chart for (e.g.) Nigeria would render a misleading sliver of
  "bioenergy only". The page gates each mix mode on a real signal
  (`fossil_fuel_consumption` for energy, `electricity_generation` for
  electricity) and the entity dropdown is filtered per mode — energy mode lists
  ~91 entities, electricity mode ~229.
- `gdp` / `energy_per_gdp` end around 2022 (OWID's Maddison-based GDP series).
- Pre-2000 electricity shares (EIA/Shift) sum to ~98%, not 100% — Ember's
  post-2000 data is cleaner. Left as-is; it's faithful to the source.
- The nine `*_electricity` columns sum exactly to `electricity_generation`; the
  nine `*_consumption` columns sum to ~`primary_energy_consumption` (substitution
  method, small residual). Verified against Israel / World / Germany.

Refresh: `python worldenergy/owid_energy_export.py`, then commit the regenerated
`energy_data.json`.
