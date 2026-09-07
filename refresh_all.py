#!/usr/bin/env python3
"""
refresh_all.py
==============
One-shot refresh of every pre-fetched data file behind madadim.net, then
commit + push whatever actually changed.

This is the automated version of the "Refreshing all data" section of
README.md. It:

  1. Runs each ``fetch_*.py`` / updater script in turn (repo root), plus
     ``worldenergy/owid_energy_export.py``.
  2. Runs the lobbyist pipeline that lives outside this repo
     (``../knesset/lobbyists/``): ``refresh_lobbyists.py --yes`` to bring the
     Postgres registry up to date, then ``export_lobbyist_data.py`` to
     regenerate ``lobbyists/lobbyists_data.json`` here.
  3. For every output file git now reports as modified, compares the new
     content against HEAD with volatile timestamp keys stripped. If nothing
     but a timestamp changed, the file is reverted (``git checkout --``) so
     the history doesn't churn multi-MB minified JSON for a no-op.
  4. If any real change survives, ``git add`` those files, commit with a
     generated message, and ``git push origin main`` (GitHub Pages deploys
     straight from main).

A single failing fetch script does NOT abort the run - the others still run
and their changes are still committed. The process exit code is non-zero if
any job failed, so Task Scheduler's "Last Run Result" stays meaningful.

Usage:
    python refresh_all.py              # full run: refresh, commit, push
    python refresh_all.py --no-push    # refresh + commit, leave the push
    python refresh_all.py --no-git     # refresh only, touch nothing in git
    python refresh_all.py --dry-run    # run the fetch scripts, then report
                                       #   what would be committed; revert all
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent
LOBBY_DIR = REPO.parent / "knesset" / "lobbyists"
PY = sys.executable  # same interpreter that launched this script

# Under Task Scheduler our stdout is a redirected file with the OS-default
# (cp1252) encoding; the fetch scripts print em-dashes etc. Force UTF-8 both
# for our own output and for every child process.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
CHILD_ENV = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}

# (label, working dir, [command args], output file relative to REPO or None)
JOBS = [
    ("Bank of Israel FX",        REPO,      ["boi_update.py"],                 "boi_rates.json"),
    ("World Bank Pink Sheet",    REPO,      ["pink_sheet_update.py"],          "pink_sheet.json"),
    ("Eurostat agri prices",     REPO,      ["fetch_eurostat_agri.py"],        "eurostat_agri.json"),
    ("Eurostat HICP (frozen)",   REPO,      ["fetch_eurostat_hicp.py"],        "eurostat_hicp.json"),
    ("Eurostat HICP (live)",     REPO,      ["fetch_eurostat_hicp_live.py"],   "eurostat_hicp_live.json"),
    ("CBS avg consumer prices",  REPO,      ["fetch_cbs_avg_prices.py"],       "cbs_avg_prices.json"),
    ("Yahoo Finance futures",    REPO,      ["fetch_yahoo_futures.py"],        "futures.json"),
    ("USDA MARS dairy",          REPO,      ["fetch_usda_dairy.py"],           "usda_dairy.json"),
    ("BLS CPI-U food items",     REPO,      ["fetch_bls_food_cpi.py"],         "bls_food_cpi.json"),
    ("OWID world energy",        REPO,      ["worldenergy/owid_energy_export.py"], "worldenergy/energy_data.json"),
    ("Lobbyist registry -> DB",  LOBBY_DIR, ["refresh_lobbyists.py", "--yes"], None),
    ("Lobbyist site JSON",       LOBBY_DIR, ["export_lobbyist_data.py"],       "lobbyists/lobbyists_data.json"),
]

# Top-level JSON keys whose value is a bare ISO date / datetime are treated as
# "when this file was generated" metadata and ignored when deciding whether the
# data really changed.
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}([ T].*)?$")


def run_job(label, cwd, args):
    """Run one script, echoing its combined output. Returns (ok, seconds)."""
    print(f"\n{'='*72}\n>>> {label}\n    {PY} {' '.join(args)}  (cwd={cwd})\n{'='*72}", flush=True)
    start = time.time()
    try:
        proc = subprocess.run(
            [PY, "-u", *args], cwd=str(cwd), timeout=1800, env=CHILD_ENV,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
        )
        if proc.stdout:
            print(proc.stdout, end="" if proc.stdout.endswith("\n") else "\n", flush=True)
        ok = proc.returncode == 0
    except subprocess.TimeoutExpired as exc:
        if exc.output:
            print(exc.output, flush=True)
        print(f"!!! {label}: TIMED OUT after 1800s", flush=True)
        ok = False
    except Exception as exc:  # noqa: BLE001 - want everything logged, nothing fatal
        print(f"!!! {label}: {type(exc).__name__}: {exc}", flush=True)
        ok = False
    secs = time.time() - start
    print(f"--- {label}: {'OK' if ok else 'FAILED'} in {secs:.0f}s", flush=True)
    return ok, secs


def git(*args, capture=True):
    return subprocess.run(
        ["git", *args], cwd=str(REPO),
        capture_output=capture, text=True, encoding="utf-8",
    )


def _strip_volatile(obj):
    """Return a copy of a top-level dict with bare-date string values removed."""
    if not isinstance(obj, dict):
        return obj
    return {
        k: v for k, v in obj.items()
        if not (isinstance(v, str) and _ISO_DATE.match(v.strip()))
    }


def only_timestamp_changed(path_rel):
    """True if `path_rel` differs from HEAD only in timestamp-ish metadata."""
    head = git("show", f"HEAD:{path_rel}")
    if head.returncode != 0:
        return False  # new file - a real change
    try:
        old = json.loads(head.stdout)
        new = json.loads((REPO / path_rel).read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return False
    a = json.dumps(_strip_volatile(old), sort_keys=True, ensure_ascii=False)
    b = json.dumps(_strip_volatile(new), sort_keys=True, ensure_ascii=False)
    return a == b


def dirty_paths():
    """Every path git reports in `status --porcelain` (tracked-modified or untracked)."""
    out = git("status", "--porcelain").stdout.splitlines()
    return [line[3:].strip() for line in out if line.strip()]


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-push", action="store_true", help="commit but do not push")
    ap.add_argument("--no-git", action="store_true", help="refresh only; leave git alone")
    ap.add_argument("--dry-run", action="store_true",
                    help="refresh, report what would be committed, then revert everything")
    args = ap.parse_args()

    started = datetime.now(timezone.utc)
    print(f"refresh_all.py starting {started:%Y-%m-%d %H:%M:%S} UTC")
    print(f"  repo      : {REPO}")
    print(f"  lobbyists : {LOBBY_DIR}")
    print(f"  python    : {PY}")

    if not args.no_git:
        pre = dirty_paths()
        if pre:
            print("\n!!! working tree not clean before run - aborting so nothing "
                  "unrelated gets committed:")
            for f in pre:
                print(f"      {f}")
            return 3

    results = []
    for label, cwd, job_args, _out in JOBS:
        ok, secs = run_job(label, cwd, job_args)
        results.append((label, ok, secs))

    failed = [lbl for lbl, ok, _ in results if not ok]

    print(f"\n{'='*72}\nJOB SUMMARY\n{'='*72}")
    for label, ok, secs in results:
        print(f"  {'ok  ' if ok else 'FAIL'}  {label:<28}  {secs:6.0f}s")

    if args.no_git:
        print("\n--no-git: leaving the working tree as-is.")
        return 1 if failed else 0

    # Decide which modified files carry a real data change.
    changed = git("status", "--porcelain").stdout.splitlines()
    touched = [ln[3:].strip() for ln in changed if ln.strip()]
    real, noop = [], []
    for f in touched:
        if f.endswith(".json") and only_timestamp_changed(f):
            noop.append(f)
        else:
            real.append(f)

    if noop:
        print("\nReverting timestamp-only changes:")
        for f in noop:
            print(f"      {f}")
        git("checkout", "--", *noop)

    if not real:
        print("\nNo real data changes. Nothing to commit.")
        return 1 if failed else 0

    print("\nFiles with real changes:")
    for f in real:
        print(f"      {f}")

    if args.dry_run:
        print("\n--dry-run: reverting the above and exiting.")
        git("checkout", "--", *real)
        return 1 if failed else 0

    git("add", *real, capture=False)
    stamp = started.strftime("%Y-%m-%d")
    lines = [f"Refresh madadim data ({stamp})", ""]
    lines += [f"- {f}" for f in real]
    if failed:
        lines += ["", "Fetch scripts that FAILED this run (not committed):"]
        lines += [f"- {lbl}" for lbl in failed]
    if noop:
        lines += ["", "Re-ran, no upstream change (reverted):"]
        lines += [f"- {f}" for f in noop]
    lines += [
        "",
        "Automated by refresh_all.py via Task Scheduler \\madadim\\madadim data refresh.",
        "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>",
    ]
    msg = "\n".join(lines)
    cm = git("commit", "-m", msg)
    print(cm.stdout, cm.stderr)
    if cm.returncode != 0:
        print("!!! commit failed")
        return 2

    if args.no_push:
        print("--no-push: committed locally, not pushing.")
        return 1 if failed else 0

    ps = git("push", "origin", "main")
    print(ps.stdout, ps.stderr)
    if ps.returncode != 0:
        print("!!! push failed")
        return 2

    print("\nPushed to origin/main - GitHub Pages will redeploy madadim.net.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
