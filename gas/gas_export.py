"""
One-off/occasional export: reads gas_global.db and writes gas_data.json
for the madadim.net/gas page. Re-run this whenever bp_stats or
igu_prices_full are updated, then copy gas_data.json into the gas/
folder next to index.html.
"""

import sqlite3
import json
from datetime import date, timezone, datetime

# ---------------------------------------------------------------------------
db_path = r"C:\Users\Ariel\PycharmProjects\MyPythonScripts\global_gas_stats\gas_global.db"
output_path = r"C:\Users\Ariel\PycharmProjects\MyPythonScripts\cbs\gas\gas_data.json"
# ---------------------------------------------------------------------------

def to_number(val):
    """Coerce a DB value to float, or None if it isn't a usable number.
    SQLite doesn't enforce column types, so numeric columns can still
    contain '', 'n/a', or numbers with stray commas."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip().replace(",", "")
    if s == "" or s.lower() in ("n/a", "na", "none", "-"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


con = sqlite3.connect(db_path)
cur = con.cursor()

cur.execute("SELECT country, year, production, consumption FROM bp_stats")
bp_rows = cur.fetchall()

cur.execute("SELECT country, year, wholesale_price FROM igu_prices_full")
price_rows = cur.fetchall()

con.close()

# country -> year -> {production, consumption, price}
by_country = {}

for row in bp_rows:
    country, year, production, consumption = row
    if country is None or year is None:
        continue
    by_country.setdefault(country, {}).setdefault(year, {})
    by_country[country][year]["production"] = to_number(production)
    by_country[country][year]["consumption"] = to_number(consumption)

for row in price_rows:
    country, year, wholesale_price = row
    if country is None or year is None:
        continue
    by_country.setdefault(country, {}).setdefault(year, {})
    by_country[country][year]["price"] = to_number(wholesale_price)

# Build the final structure: one sorted array of yearly records per country,
# with imports = consumption - production (only when both are present).
countries = {}
for country, years in by_country.items():
    records = []
    for year in sorted(years.keys()):
        rec = years[year]
        production = rec.get("production")
        consumption = rec.get("consumption")
        price = rec.get("price")
        imports = (
            consumption - production
            if production is not None and consumption is not None
            else None
        )
        records.append(
            {
                "year": year,
                "production": production,
                "consumption": consumption,
                "imports": imports,
                "price": price,
            }
        )
    countries[country] = records

output = {
    "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    "countries": countries,
}

with open(output_path, "w", encoding="utf-8") as f:
    json.dump(output, f, ensure_ascii=False, separators=(",", ":"))

print(f"Wrote {len(countries)} countries to {output_path}")