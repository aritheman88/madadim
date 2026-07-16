"""
browse_slugs.py

Searches the master report list (pulled once and saved as master_report_list_sample.json)
to find candidate slug_ids for products you care about, without re-hitting the API.

Usage:
    python browse_slugs.py                              -> lists all dairy-market-type slugs
    python browse_slugs.py cheese                        -> one search term
    python browse_slugs.py wheat corn sugar rice cocoa    -> multiple terms, results grouped per term

Each argument is treated as a SEPARATE search (not OR'd together) — results are printed
under their own heading, with a per-term count. This makes it easy to scan a whole shopping
list of commodities at once and immediately spot which ones have no USDA MARS coverage.

If master_report_list_sample.json isn't found locally, it re-fetches from the API and saves it
(same shape as the original sample script) before searching.
"""

import json
import sys
import requests
from pathlib import Path

from config import usda_key

API_BASE = "https://marsapi.ams.usda.gov/services/v1.2/reports"
MASTER_LIST_PATH = Path(__file__).parent / "master_report_list_sample.json"


def load_or_fetch_master_list() -> list:
    if MASTER_LIST_PATH.exists():
        with open(MASTER_LIST_PATH, "r", encoding="utf-8") as f:
            payload = json.load(f)
    else:
        print("No local master list found, fetching from API...")
        response = requests.get(API_BASE, auth=(usda_key, ""), timeout=30)
        response.raise_for_status()
        payload = response.json()
        with open(MASTER_LIST_PATH, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    # Master list endpoint also returns {"results": [...]}
    return payload.get("results", payload) if isinstance(payload, dict) else payload


DAIRY_MARKET_TYPES = {
    "point of sale - dairy",
    "cold storage",
    "retail",
    "direct to consumer",
    "farmers market",
}


def dedupe_by_slug(entries: list) -> dict:
    seen = {}
    for e in entries:
        sid = e.get("slug_id")
        if sid not in seen:
            seen[sid] = e
    return seen


def print_entries(seen: dict):
    if not seen:
        print("  (no matches)")
        return
    for sid, e in sorted(seen.items(), key=lambda kv: int(kv[0])):
        mtypes = ", ".join(e.get("market_types", []))
        print(f"  slug_id={sid:<6} | {e.get('report_title','?'):<45} | "
              f"market_types={mtypes:<30} | offices={', '.join(e.get('offices', []))}")


def search_term(entries: list, term: str) -> dict:
    term_lower = term.lower()
    matches = [e for e in entries if term_lower in str(e.get("report_title", "")).lower()]
    return dedupe_by_slug(matches)


def main():
    terms = sys.argv[1:]
    entries = load_or_fetch_master_list()
    print(f"Loaded {len(entries)} total report entries.\n")

    if not terms:
        # No search terms given -> default view: all dairy-market-type slugs
        dairy_market_type = [
            e for e in entries
            if any((mt or "").lower() in DAIRY_MARKET_TYPES for mt in e.get("market_types", []))
        ]
        seen = dedupe_by_slug(dairy_market_type)
        print(f"Entries with a dairy-associated market_type: {len(seen)}\n")
        print_entries(seen)
        return

    total_unique_slugs = set()
    for term in terms:
        seen = search_term(entries, term)
        total_unique_slugs.update(seen.keys())
        print(f"=== '{term}' -> {len(seen)} match(es) ===")
        print_entries(seen)
        print()

    print(f"Searched {len(terms)} term(s), {len(total_unique_slugs)} unique slug(s) total across all terms.")


if __name__ == "__main__":
    main()


