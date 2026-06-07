"""
cbs_explorer.py
===============
Generates a self-contained interactive HTML explorer for all ~1,680 CBS
price indices. The catalog is embedded in the HTML; actual time-series data
is fetched on demand from the CBS API directly in your browser (Israeli IP).

Usage:
    python cbs_explorer.py                          # reads cbs_catalog.tsv
    python cbs_explorer.py --catalog my_catalog.tsv
    python cbs_explorer.py --out cbs_explorer.html
    python cbs_explorer.py --from 2010-01 --to 2026-05

Catalog TSV format (tab-separated, with header):
    Code  Name  Monthly  From  To  Subject

Requirements:
    pip install requests python-dateutil openpyxl
    Must be run from an Israeli IP address (for the HTML to fetch data).
"""

import argparse
import json
import os
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from dateutil.relativedelta import relativedelta

import requests

# ── Config ────────────────────────────────────────────────────────────────────

API_BASE = "https://api.cbs.gov.il/index/data/price"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/xml, text/xml, */*",
}

CHUNK_MONTHS = 60
REBASE_THRESHOLD = 1.0   # index-point drop at January = rebase boundary

# ── Catalog loading ───────────────────────────────────────────────────────────

def load_catalog(path: str) -> list[dict]:
    """
    Load the CBS index catalog from a TSV file.
    Deduplicates by (Code, Name) — same code can appear under multiple subjects.
    Returns list of {code, name, monthly, from_date, to_date, subject}.
    """
    seen = set()
    rows = []
    with open(path, encoding="utf-8") as f:
        lines = f.readlines()

    # Skip header
    for line in lines[1:]:
        parts = line.rstrip("\n").split("\t")
        if len(parts) < 5:
            continue
        code     = parts[0].strip()
        name     = parts[1].strip()
        monthly  = parts[2].strip().lower() == "true"
        from_d   = parts[3].strip()[:7] if len(parts) > 3 else ""  # YYYY-MM
        to_d     = parts[4].strip()[:7] if len(parts) > 4 else ""
        subject  = parts[5].strip()     if len(parts) > 5 else ""

        if not code or not name:
            continue
        if not monthly:
            continue   # skip non-monthly (quarterly, etc.)

        key = (code, name)
        if key in seen:
            continue
        seen.add(key)

        rows.append({
            "code":    code,
            "name":    name,
            "from":    from_d,
            "to":      to_d,
            "subject": subject,
        })

    rows.sort(key=lambda r: r["name"])
    return rows


# ── Fetch helpers (used for pre-seeded indices only) ──────────────────────────

def cbs_period(ym: str) -> str:
    y, m = ym.split("-")
    return f"{m}-{y}"


def fetch_chunk(index_id: str, from_ym: str, to_ym: str) -> list[dict]:
    params = {
        "id":          index_id,
        "startPeriod": cbs_period(from_ym),
        "endPeriod":   cbs_period(to_ym),
        "format":      "xml",
        "lang":        "he",
        "baseType":    "1",
    }
    try:
        r = requests.get(API_BASE, params=params, headers=HEADERS, timeout=30)
        r.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"    [ERROR] {e}", file=sys.stderr)
        return []

    text = r.text.strip()
    if not text.startswith("<"):
        return []
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return []

    rows = []
    for dm in root.iter("DateMonth"):
        def txt(tag):
            el = dm.find(tag)
            return (el.text or "").strip() if el is not None else ""

        year  = txt("year")
        month = txt("month").zfill(2)
        base  = dm.find("currBase")
        value_str = ""
        if base is not None:
            ve = base.find("value")
            value_str = (ve.text or "").strip() if ve is not None else ""
        pct_str = txt("percentYear")

        if not (year and month and value_str):
            continue
        try:
            value = float(value_str)
        except ValueError:
            continue

        pct_year = None
        try:
            pct_year = float(pct_str)
        except ValueError:
            pass

        rows.append({"date": f"{year}-{month}", "value": value, "pct_year": pct_year})

    rows.sort(key=lambda r: r["date"])
    return rows


def fetch_all_chunks(index_id: str, from_ym: str, to_ym: str) -> list[dict]:
    all_rows, seen = [], set()
    cursor = datetime.strptime(from_ym, "%Y-%m")
    end    = datetime.strptime(to_ym,   "%Y-%m")

    while cursor <= end:
        chunk_end = min(cursor + relativedelta(months=CHUNK_MONTHS - 1), end)
        from_s = cursor.strftime("%Y-%m")
        to_s   = chunk_end.strftime("%Y-%m")
        print(f"    chunk {from_s} → {to_s} ...", end=" ", flush=True)

        rows = fetch_chunk(index_id, from_s, to_s)
        new  = [r for r in rows if r["date"] not in seen]
        seen.update(r["date"] for r in new)
        all_rows.extend(new)
        print(f"{len(new)} rows")

        cursor = chunk_end + relativedelta(months=1)
        time.sleep(0.4)

    all_rows.sort(key=lambda r: r["date"])
    return all_rows


def rescale_to_base2010(rows: list[dict]) -> list[dict]:
    if not rows:
        return rows
    rows = [dict(r) for r in rows]

    # Step 1: chain January rebase drops
    for i in range(1, len(rows)):
        if rows[i]["date"].endswith("-01"):
            prev_val = rows[i-1]["value"]
            curr_val = rows[i]["value"]
            drop = prev_val - curr_val
            if drop > REBASE_THRESHOLD:
                scale = prev_val / curr_val
                print(f"    rebase at {rows[i]['date']}: {prev_val:.3f} → {curr_val:.3f}  (×{scale:.4f})")
                for j in range(i, len(rows)):
                    rows[j]["value"] = rows[j]["value"] * scale

    # Step 2: rescale to annual average 2010 = 100
    vals_2010 = [r["value"] for r in rows if r["date"].startswith("2010")]
    if not vals_2010:
        return rows
    avg_2010 = sum(vals_2010) / len(vals_2010)
    print(f"    2010 avg={avg_2010:.4f} → rescaling to base 2010=100")
    return [dict(r, value=round(r["value"] / avg_2010 * 100, 3)) for r in rows]


# ── HTML generation ───────────────────────────────────────────────────────────

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>CBS Price Index Explorer</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns@3/dist/chartjs-adapter-date-fns.bundle.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/xlsx@0.18.5/dist/xlsx.full.min.js"></script>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&family=IBM+Plex+Sans+Hebrew:wght@300;400;600&display=swap" rel="stylesheet">
<style>
:root{--bg:#0d0f14;--surface:#161922;--border:#252a35;--text:#e2e8f0;--muted:#6b7585;--accent:#4ade80;--warn:#f59e0b;--danger:#f87171;}
*{box-sizing:border-box;margin:0;padding:0;}
body{background:var(--bg);color:var(--text);font-family:'IBM Plex Sans Hebrew',sans-serif;min-height:100vh;display:flex;flex-direction:column;}
header{border-bottom:1px solid var(--border);padding:16px 28px;display:flex;align-items:baseline;gap:14px;flex-wrap:wrap;}
header h1{font-family:'IBM Plex Mono',monospace;font-size:1rem;font-weight:600;color:var(--accent);letter-spacing:.05em;}
header p{font-size:.75rem;color:var(--muted);font-family:'IBM Plex Mono',monospace;}

.layout{display:flex;flex:1;min-height:0;}

/* ── Left panel: catalog ── */
.sidebar{width:380px;min-width:300px;border-left:1px solid var(--border);display:flex;flex-direction:column;overflow:hidden;}
.search-wrap{padding:12px 14px;border-bottom:1px solid var(--border);}
.search-wrap input{width:100%;background:var(--surface);border:1px solid var(--border);color:var(--text);padding:8px 12px;font-family:'IBM Plex Mono',monospace;font-size:.82rem;border-radius:4px;outline:none;}
.search-wrap input:focus{border-color:var(--accent);}
.search-info{font-size:.68rem;color:var(--muted);font-family:'IBM Plex Mono',monospace;margin-top:6px;}
.catalog-list{flex:1;overflow-y:auto;padding:4px 0;}
.cat-item{padding:8px 14px;cursor:pointer;border-bottom:1px solid var(--border);transition:background .15s;}
.cat-item:hover{background:var(--surface);}
.cat-item.selected{background:#1a2a1a;border-right:3px solid var(--accent);}
.cat-item .item-name{font-size:.82rem;color:var(--text);}
.cat-item .item-meta{font-size:.68rem;color:var(--muted);font-family:'IBM Plex Mono',monospace;margin-top:2px;}
.cat-item .item-code{color:var(--warn);}

/* ── Right panel: chart ── */
.main{flex:1;display:flex;flex-direction:column;min-width:0;overflow:hidden;}
.controls{padding:12px 20px;display:flex;gap:14px;align-items:flex-end;flex-wrap:wrap;border-bottom:1px solid var(--border);}
.control-group{display:flex;flex-direction:column;gap:5px;}
.control-group label{font-size:.66rem;color:var(--muted);font-family:'IBM Plex Mono',monospace;text-transform:uppercase;letter-spacing:.08em;}
.range-inputs{display:flex;align-items:center;gap:8px;}
input[type="month"]{background:var(--surface);border:1px solid var(--border);color:var(--text);padding:6px 10px;font-family:'IBM Plex Mono',monospace;font-size:.8rem;border-radius:4px;outline:none;}
input[type="month"]:focus{border-color:var(--accent);}
.sep{color:var(--muted);font-family:'IBM Plex Mono',monospace;font-size:.8rem;}
.mode-group{display:flex;gap:3px;}
.mode-btn{padding:6px 12px;border-radius:4px;border:1px solid var(--border);background:var(--surface);color:var(--muted);cursor:pointer;font-family:'IBM Plex Mono',monospace;font-size:.72rem;transition:all .2s;}
.mode-btn.active{background:var(--accent);color:var(--bg);border-color:var(--accent);font-weight:600;}
.fetch-btn{padding:7px 18px;border-radius:4px;border:none;background:var(--accent);color:var(--bg);cursor:pointer;font-family:'IBM Plex Mono',monospace;font-size:.78rem;font-weight:600;transition:opacity .2s;white-space:nowrap;}
.fetch-btn:hover{opacity:.85;}
.fetch-btn:disabled{opacity:.4;cursor:not-allowed;}
.clear-btn{padding:7px 14px;border-radius:4px;border:1px solid var(--border);background:var(--surface);color:var(--danger);cursor:pointer;font-family:'IBM Plex Mono',monospace;font-size:.72rem;transition:all .2s;}
.clear-btn:hover{border-color:var(--danger);}
.xlsx-btn{padding:7px 14px;border-radius:4px;border:1px solid #22c55e;background:var(--surface);color:#22c55e;cursor:pointer;font-family:'IBM Plex Mono',monospace;font-size:.72rem;transition:all .2s;}
.xlsx-btn:hover{background:#22c55e22;}
.xlsx-btn:disabled{opacity:.3;cursor:not-allowed;}

/* Selected chips */
.chips-wrap{padding:8px 20px;display:flex;gap:6px;flex-wrap:wrap;border-bottom:1px solid var(--border);min-height:38px;align-items:center;}
.chip{display:flex;align-items:center;gap:6px;padding:3px 10px;border-radius:12px;font-size:.72rem;font-family:'IBM Plex Mono',monospace;border:1px solid;cursor:default;}
.chip .remove{cursor:pointer;opacity:.6;font-size:.9rem;}
.chip .remove:hover{opacity:1;}
.chips-empty{font-size:.72rem;color:var(--muted);font-family:'IBM Plex Mono',monospace;}

/* Status bar */
.status-bar{padding:5px 20px;font-size:.7rem;color:var(--muted);font-family:'IBM Plex Mono',monospace;border-bottom:1px solid var(--border);min-height:26px;display:flex;align-items:center;gap:10px;}
.dot{width:7px;height:7px;border-radius:50%;background:var(--muted);display:inline-block;flex-shrink:0;}
.dot.loading{background:var(--warn);animation:pulse 1s infinite;}
.dot.ok{background:var(--accent);}
.dot.error{background:var(--danger);}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}

.chart-wrap{flex:1;padding:16px 20px;min-height:300px;}
canvas{width:100%!important;height:100%!important;}

/* Scrollbar */
.catalog-list::-webkit-scrollbar{width:6px;}
.catalog-list::-webkit-scrollbar-track{background:var(--bg);}
.catalog-list::-webkit-scrollbar-thumb{background:var(--border);border-radius:3px;}
</style>
</head>
<body>
<header>
  <h1>CBS // Price Index Explorer</h1>
  <p>הלשכה המרכזית לסטטיסטיקה · __TOTAL_INDICES__ מדדים · Generated: __GENERATED__</p>
</header>

<div class="layout">

  <!-- Left: catalog search -->
  <div class="sidebar">
    <div class="search-wrap">
      <input type="text" id="searchBox" placeholder="חיפוש לפי שם, קוד או נושא..." oninput="filterCatalog()" dir="rtl" autofocus>
      <div class="search-info" id="searchInfo"></div>
    </div>
    <div class="catalog-list" id="catalogList"></div>
  </div>

  <!-- Right: controls + chart -->
  <div class="main">
    <div class="controls">
      <div class="control-group">
        <label>טווח</label>
        <div class="range-inputs">
          <input type="month" id="fromDate" value="__FROM__">
          <span class="sep">→</span>
          <input type="month" id="toDate"   value="__TO__">
        </div>
      </div>
      <div class="control-group">
        <label>תצוגה</label>
        <div class="mode-group">
          <button class="mode-btn"        onclick="setMode('index',this)">ערך</button>
          <button class="mode-btn"        onclick="setMode('pct',this)">% שנתי</button>
          <button class="mode-btn active" onclick="setMode('base',this)">% מבסיס</button>
        </div>
      </div>
      <button class="fetch-btn" id="fetchBtn" onclick="fetchSelected()">טען נתונים ▶</button>
      <button class="clear-btn" onclick="clearAll()">נקה הכל</button>
      <button class="xlsx-btn" id="xlsxBtn" onclick="downloadXlsx()" disabled>⬇ XLSX</button>
    </div>

    <div class="chips-wrap" id="chipsWrap">
      <span class="chips-empty">בחר מדדים מהרשימה משמאל</span>
    </div>

    <div class="status-bar">
      <span class="dot" id="dot"></span>
      <span id="statusText">מוכן</span>
    </div>

    <div class="chart-wrap"><canvas id="myChart"></canvas></div>
  </div>
</div>

<script>
// ── Catalog data ──────────────────────────────────────────────────────────────
const CATALOG = __CATALOG_JSON__;

// ── State ─────────────────────────────────────────────────────────────────────
const COLORS = [
  "#f59e0b","#38bdf8","#4ade80","#f87171","#a78bfa",
  "#fb923c","#34d399","#60a5fa","#f472b6","#facc15",
  "#22d3ee","#c084fc","#86efac","#fca5a5","#93c5fd",
];
let selectedMap = {};   // code -> {name, color, data: null}
let colorIdx    = 0;
let mode        = "base";
let chart       = null;
let filtered    = [];

// ── Catalog rendering ─────────────────────────────────────────────────────────
function filterCatalog() {
  const q = document.getElementById("searchBox").value.trim().toLowerCase();
  filtered = q
    ? CATALOG.filter(r =>
        r.name.toLowerCase().includes(q) ||
        r.code.includes(q) ||
        r.subject.toLowerCase().includes(q))
    : CATALOG;

  const list = document.getElementById("catalogList");
  const info = document.getElementById("searchInfo");
  info.textContent = `${filtered.length.toLocaleString()} מדדים מוצגים`;

  // Virtualise: only render first 200 for perf
  const slice = filtered.slice(0, 200);
  list.innerHTML = slice.map(r => {
    const sel = selectedMap[r.code] ? "selected" : "";
    const color = selectedMap[r.code]?.color ?? "";
    const border = color ? `border-right:3px solid ${color};` : "";
    return `<div class="cat-item ${sel}" style="${border}" onclick="toggleIndex('${r.code}','${esc(r.name)}')">
      <div class="item-name">${hl(r.name, document.getElementById("searchBox").value)}</div>
      <div class="item-meta"><span class="item-code">${r.code}</span> · ${esc(r.subject).slice(0,50)}</div>
    </div>`;
  }).join("");

  if (filtered.length > 200)
    list.innerHTML += `<div style="padding:10px 14px;font-size:.7rem;color:var(--muted);font-family:'IBM Plex Mono',monospace;">... ועוד ${filtered.length - 200} תוצאות — צמצם את החיפוש</div>`;
}

function esc(s){ return s.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;"); }

function hl(text, q) {
  if (!q) return esc(text);
  const re = new RegExp(q.replace(/[.*+?^${}()|[\]\\]/g,"\\$&"), "gi");
  return esc(text).replace(re, m => `<mark style="background:#f59e0b33;color:var(--warn)">${m}</mark>`);
}

function toggleIndex(code, name) {
  if (selectedMap[code]) {
    delete selectedMap[code];
  } else {
    if (Object.keys(selectedMap).length >= 10) {
      setStatus("", "מקסימום 10 מדדים בו-זמנית");
      return;
    }
    selectedMap[code] = { name, color: COLORS[colorIdx % COLORS.length], data: null };
    colorIdx++;
  }
  renderChips();
  filterCatalog();
}

// ── Chips ─────────────────────────────────────────────────────────────────────
function renderChips() {
  const wrap = document.getElementById("chipsWrap");
  const entries = Object.entries(selectedMap);
  if (!entries.length) {
    wrap.innerHTML = '<span class="chips-empty">בחר מדדים מהרשימה משמאל</span>';
    return;
  }
  wrap.innerHTML = entries.map(([code, {name, color}]) =>
    `<div class="chip" style="border-color:${color};color:${color}">
      <span>${esc(name)} (${code})</span>
      <span class="remove" onclick="removeIndex('${code}')">✕</span>
    </div>`
  ).join("");
}

function removeIndex(code) {
  delete selectedMap[code];
  renderChips();
  filterCatalog();
  if (chart) updateChart();
}

function clearAll() {
  selectedMap = {};
  colorIdx = 0;
  renderChips();
  filterCatalog();
  if (chart) { chart.destroy(); chart = null; }
  document.getElementById("xlsxBtn").disabled = true;
  setStatus("", "מוכן");
}

// ── Fetch ─────────────────────────────────────────────────────────────────────
const API = "https://api.cbs.gov.il/index/data/price";

function cbsPeriod(ym) {
  const [y,m] = ym.split("-");
  return `${m}-${y}`;
}

async function fetchIndex(code, fromYm, toYm) {
  // Fetch in 60-month chunks
  const chunks = [];
  let cursor = new Date(fromYm + "-01");
  const end   = new Date(toYm  + "-01");

  while (cursor <= end) {
    const chunkEnd = new Date(cursor);
    chunkEnd.setMonth(chunkEnd.getMonth() + 59);
    if (chunkEnd > end) chunkEnd.setTime(end.getTime());

    const from_s = cursor.toISOString().slice(0,7);
    const to_s   = chunkEnd.toISOString().slice(0,7);

    const url = `${API}?id=${code}&startPeriod=${cbsPeriod(from_s)}&endPeriod=${cbsPeriod(to_s)}&format=xml&lang=he&baseType=1`;
    const res = await fetch(url, { headers: { Accept: "application/xml,text/xml,*/*" } });
    if (!res.ok) throw new Error(`HTTP ${res.status} for ${code}`);
    const text = await res.text();
    const xml  = new DOMParser().parseFromString(text, "application/xml");
    if (xml.querySelector("parsererror")) throw new Error("XML parse error");

    xml.querySelectorAll("DateMonth").forEach(dm => {
      const year  = dm.querySelector("year")?.textContent?.trim();
      const month = dm.querySelector("month")?.textContent?.trim().padStart(2,"0");
      const base  = dm.querySelector("currBase");
      const val   = parseFloat(base?.querySelector("value")?.textContent);
      const pct   = parseFloat(dm.querySelector("percentYear")?.textContent);
      if (year && month && !isNaN(val))
        chunks.push({ date:`${year}-${month}`, value:val, pct_year:isNaN(pct)?null:pct });
    });

    cursor.setMonth(cursor.getMonth() + 60);
  }

  // Deduplicate and sort
  const seen = new Set(), rows = [];
  chunks.sort((a,b) => a.date.localeCompare(b.date));
  for (const r of chunks) {
    if (!seen.has(r.date)) { seen.add(r.date); rows.push(r); }
  }

  // Chain rebase boundaries (January drops > 1 point)
  for (let i = 1; i < rows.length; i++) {
    if (rows[i].date.endsWith("-01")) {
      const drop = rows[i-1].value - rows[i].value;
      if (drop > 1.0) {
        const scale = rows[i-1].value / rows[i].value;
        for (let j = i; j < rows.length; j++) rows[j].value *= scale;
      }
    }
  }

  // Rescale to base 2010=100
  const v2010 = rows.filter(r => r.date.startsWith("2010")).map(r => r.value);
  if (v2010.length) {
    const avg = v2010.reduce((a,b) => a+b, 0) / v2010.length;
    rows.forEach(r => r.value = Math.round(r.value / avg * 1000) / 10);
  }

  return rows;
}

async function fetchSelected() {
  const codes = Object.keys(selectedMap);
  if (!codes.length) { setStatus("","בחר מדד אחד לפחות"); return; }

  const fromYm = document.getElementById("fromDate").value;
  const toYm   = document.getElementById("toDate").value;
  const btn    = document.getElementById("fetchBtn");
  btn.disabled = true;

  setStatus("loading", `טוען ${codes.length} מדדים...`);

  let done = 0;
  for (const code of codes) {
    setStatus("loading", `טוען ${selectedMap[code].name} (${code})... [${done+1}/${codes.length}]`);
    try {
      selectedMap[code].data = await fetchIndex(code, fromYm, toYm);
      done++;
    } catch(e) {
      setStatus("error", `שגיאה בטעינת ${code}: ${e.message}`);
      btn.disabled = false;
      return;
    }
  }

  setStatus("ok", `נטענו ${codes.length} מדדים · ${fromYm} → ${toYm}`);
  btn.disabled = false;
  document.getElementById("xlsxBtn").disabled = false;
  updateChart();
}

// ── Chart ─────────────────────────────────────────────────────────────────────
function filtered_data(rows) {
  const from = document.getElementById("fromDate").value;
  const to   = document.getElementById("toDate").value;
  return (rows || []).filter(r => r.date >= from && r.date <= to);
}

function buildSeries(rows) {
  const r = filtered_data(rows);
  if (!r.length) return [];
  if (mode === "index") return r.map(x => ({ x: new Date(x.date+"-01"), y: x.value }));
  if (mode === "pct")   return r.map(x => ({ x: new Date(x.date+"-01"), y: x.pct_year }));
  if (mode === "base") {
    const base = r[0].value;
    return r.map(x => ({ x: new Date(x.date+"-01"), y: +((x.value/base-1)*100).toFixed(2) }));
  }
}

function updateChart() {
  const entries = Object.entries(selectedMap).filter(([,v]) => v.data);
  if (!entries.length) return;

  const datasets = entries.map(([code, {name, color, data}]) => ({
    label:            `${name} (${code})`,
    data:             buildSeries(data),
    borderColor:      color,
    backgroundColor:  color + "18",
    borderWidth:      1.8,
    pointRadius:      0,
    pointHoverRadius: 4,
    tension:          0.2,
    fill:             false,
  }));

  const yLabel = mode==="index" ? "ערך מדד (בסיס 2010=100)" : mode==="pct" ? "% שינוי שנתי" : "% מבסיס";

  if (chart) {
    chart.data.datasets = datasets;
    chart.options.scales.y.title.text = yLabel;
    chart.update();
    return;
  }

  chart = new Chart(document.getElementById("myChart"), {
    type: "line",
    data: { datasets },
    options: {
      responsive:true, maintainAspectRatio:false, animation:{duration:300},
      interaction:{mode:"index",intersect:false},
      plugins:{
        legend:{display:true,labels:{color:"#e2e8f0",font:{family:"'IBM Plex Mono',monospace",size:10},boxWidth:12,padding:8}},
        tooltip:{
          backgroundColor:"#161922",borderColor:"#252a35",borderWidth:1,
          titleColor:"#6b7585",bodyColor:"#e2e8f0",
          titleFont:{family:"'IBM Plex Mono',monospace",size:10},
          bodyFont: {family:"'IBM Plex Mono',monospace",size:11},
          padding:10,
          callbacks:{label:ctx=>{
            const v=ctx.parsed.y;
            return v==null?`${ctx.dataset.label}: N/A`:`${ctx.dataset.label}: ${v.toFixed(2)}${mode==="index"?"":"%"}`;
          }}
        }
      },
      scales:{
        x:{
          type:"time",
          time:{unit:"year",tooltipFormat:"yyyy-MM",displayFormats:{year:"yyyy",month:"MMM yy"}},
          ticks:{color:"#6b7585",font:{family:"'IBM Plex Mono',monospace",size:10},maxRotation:0},
          grid:{color:"#252a35"},
        },
        y:{
          ticks:{color:"#6b7585",font:{family:"'IBM Plex Mono',monospace",size:10}},
          grid:{color:"#252a35"},
          title:{display:true,text:yLabel,color:"#6b7585",font:{family:"'IBM Plex Mono',monospace",size:10}},
          afterDataLimits(axis){
            const range=axis.max-axis.min;
            axis.min=axis.min-range*.05;
            axis.max=axis.max+range*.05;
          }
        }
      }
    }
  });
}

function setMode(m, btn) {
  mode = m;
  document.querySelectorAll(".mode-btn").forEach(b => b.classList.remove("active"));
  btn.classList.add("active");
  if (chart) updateChart();
}

function setStatus(state, msg) {
  document.getElementById("dot").className = "dot " + state;
  document.getElementById("statusText").textContent = msg;
}

document.getElementById("fromDate").addEventListener("change", () => { if(chart) updateChart(); });
document.getElementById("toDate").addEventListener("change",   () => { if(chart) updateChart(); });


function downloadXlsx() {
  const entries = Object.entries(selectedMap).filter(([,v]) => v.data && v.data.length);
  if (!entries.length) return;

  const fromYm = document.getElementById("fromDate").value;
  const toYm   = document.getElementById("toDate").value;

  const wb = XLSX.utils.book_new();

  // Sheet 1: wide format — date | index1 value | index1 pct | index2 value | ...
  const allDates = [...new Set(
    entries.flatMap(([,v]) => filtered_data(v.data).map(r => r.date))
  )].sort();

  const headers = ["תאריך"];
  entries.forEach(([code, {name}]) => {
    headers.push(`${name} (${code}) — ערך`);
    headers.push(`${name} (${code}) — % שנתי`);
  });

  const rows = [headers];
  allDates.forEach(date => {
    const row = [date];
    entries.forEach(([code, {data}]) => {
      const fd = filtered_data(data);
      const r = fd.find(x => x.date === date);
      row.push(r ? r.value     : "");
      row.push(r ? (r.pct_year ?? "") : "");
    });
    rows.push(row);
  });

  const ws1 = XLSX.utils.aoa_to_sheet(rows);
  // Column widths
  ws1["!cols"] = [{ wch: 10 }, ...entries.flatMap(() => [{ wch: 28 }, { wch: 14 }])];
  XLSX.utils.book_append_sheet(wb, ws1, "נתונים");

  // Sheet 2: % change from base (first date in range)
  const headers2 = ["תאריך"];
  entries.forEach(([code, {name}]) => headers2.push(`${name} (${code}) — % מבסיס`));
  const rows2 = [headers2];
  allDates.forEach(date => {
    const row = [date];
    entries.forEach(([code, {data}]) => {
      const fd = filtered_data(data);
      if (!fd.length) { row.push(""); return; }
      const base = fd[0].value;
      const r = fd.find(x => x.date === date);
      row.push(r ? +((r.value / base - 1) * 100).toFixed(2) : "");
    });
    rows2.push(row);
  });
  const ws2 = XLSX.utils.aoa_to_sheet(rows2);
  ws2["!cols"] = [{ wch: 10 }, ...entries.map(() => ({ wch: 28 }))];
  XLSX.utils.book_append_sheet(wb, ws2, "% מבסיס");

  // Filename
  const fname = `cbs_indices_${fromYm}_${toYm}.xlsx`;
  XLSX.writeFile(wb, fname);
}

// ── Init ──────────────────────────────────────────────────────────────────────
filterCatalog();
</script>
</body>
</html>
"""


def generate_html(catalog: list[dict], from_ym: str, to_ym: str, out_path: str):
    total      = len(catalog)
    generated  = datetime.now().strftime("%Y-%m-%d %H:%M")

    html = HTML_TEMPLATE
    html = html.replace("__CATALOG_JSON__",  json.dumps(catalog, ensure_ascii=False))
    html = html.replace("__TOTAL_INDICES__", str(total))
    html = html.replace("__GENERATED__",     generated)
    html = html.replace("__FROM__",          from_ym)
    html = html.replace("__TO__",            to_ym)

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  → Saved: {out_path}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="CBS Price Index Explorer — full catalog HTML")
    parser.add_argument("--catalog", default="cbs_catalog.tsv",
                        help="Path to catalog TSV file (default: cbs_catalog.tsv)")
    parser.add_argument("--from",    dest="from_ym", default="2010-01")
    parser.add_argument("--to",      dest="to_ym",
                        default=datetime.now().strftime("%Y-%m"))
    parser.add_argument("--out",     default="cbs_explorer.html")
    args = parser.parse_args()

    base_dir  = os.path.dirname(os.path.abspath(__file__))
    cat_path  = os.path.join(base_dir, args.catalog)
    out_path  = os.path.join(base_dir, args.out)

    if not os.path.exists(cat_path):
        print(f"ERROR: catalog file not found: {cat_path}")
        print("Save the catalog data as a tab-separated file (TSV) with header:")
        print("  Code\\tName\\tMonthly\\tFrom\\tTo\\tSubject")
        sys.exit(1)

    print(f"Loading catalog from {cat_path}...")
    catalog = load_catalog(cat_path)
    print(f"  {len(catalog)} monthly indices loaded")

    print("Generating HTML...")
    generate_html(catalog, args.from_ym, args.to_ym, out_path)
    print("Done.\n")
    print("Open the HTML file in your browser (Israeli IP required to fetch data).")


if __name__ == "__main__":
    main()