"""
usda_fetch.py

Pulls full historical data for a configured list of USDA MARS (My Market News)
report slugs and saves each as a tidy JSON file.

Each API call returns the FULL history for that slug (not just the latest report),
wrapped as {"stats": {...}, "results": [...]}. We unwrap to just the results list
and save flat JSON per slug, which is easy to load into pandas / Postgres later.

Usage:
    python usda_fetch.py

Add/remove slugs in SLUGS below. Run daily via Task Scheduler — biweekly slugs will
just re-save the same data until their next release, which is harmless and lets you
use one schedule for everything regardless of each report's own cadence.
"""

import json
import requests
from pathlib import Path
from datetime import datetime, timezone

from config import usda_key

# ---- Config ----

API_BASE = "https://marsapi.ams.usda.gov/services/v1.2/reports"
OUTPUT_DIR = Path(__file__).parent / "data"
OUTPUT_DIR.mkdir(exist_ok=True)

# slug_id -> short label (label is just for filenames/logging, the real metadata
# comes back in the JSON itself: report_title, commodity, region, etc.)
SLUGS = {
    1098: "butter_butteroil_europe",
    # Add more slug IDs here as you identify them from the Dairy Master Report List, e.g.:
    # 2987: "cheese_europe",
    # 3456: "milk_powder_oceania",
}


def fetch_slug(slug_id: int) -> dict:
    """Fetch full results for one slug. Returns the unwrapped 'results' list plus stats."""
    url = f"{API_BASE}/{slug_id}"
    response = requests.get(url, auth=(usda_key, ""), timeout=30)
    response.raise_for_status()
    payload = response.json()
    return payload


def save_slug_data(slug_id: int, label: str, payload: dict) -> Path:
    stats = payload.get("stats", {})
    results = payload.get("results", [])

    out = {
        "slug_id": slug_id,
        "label": label,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "row_count": stats.get("returnedRows", len(results)),
        "results": results,
    }

    out_path = OUTPUT_DIR / f"{slug_id}_{label}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    return out_path


def main():
    print(f"Fetching {len(SLUGS)} slug(s)...\n")

    for slug_id, label in SLUGS.items():
        print(f"[{slug_id}] {label} ... ", end="", flush=True)
        try:
            payload = fetch_slug(slug_id)
        except requests.exceptions.HTTPError as e:
            print(f"FAILED ({e})")
            print(f"  Response body: {e.response.text[:300]}")
            continue
        except requests.exceptions.RequestException as e:
            print(f"FAILED (connection issue: {e})")
            continue

        stats = payload.get("stats", {})
        n_rows = stats.get("returnedRows", "?")
        n_total = stats.get("totalRows", "?")

        out_path = save_slug_data(slug_id, label, payload)
        print(f"OK — {n_rows}/{n_total} rows -> {out_path.name}")

        # Flag if we're hitting the row cap (would mean some history is being truncated)
        user_allowed = stats.get("userAllowedRows")
        if isinstance(n_total, int) and isinstance(user_allowed, int) and n_total >= user_allowed:
            print(f"  WARNING: totalRows ({n_total}) >= userAllowedRows ({user_allowed}) "
                  f"— history may be truncated, consider filtering by date range.")

    print("\nDone.")


if __name__ == "__main__":
    main()