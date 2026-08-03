#!/usr/bin/env python3
"""
Convert analyze.py plain-text output to styled HTML.
Usage: python3 analyze.py | python3 to_html.py > report.html
       python3 to_html.py < report.txt > report.html
"""
import sys, re, html as H, json as _json, os as _os, urllib.request as _urlreq

# ─── CSS ──────────────────────────────────────────────────────────────────────
CSS = """
:root {
  /* Palette — chosen, not defaulted */
  --ink:      #111d2c;   /* header, heavy text */
  --lead:     #1a5fa8;   /* accent: col headers, section stripe, bar fills */
  --chalk:    #edf0f4;   /* page background — cool slate, not warm cream */
  --card:     #ffffff;
  --rule:     #c6d0dd;   /* table borders, dividers */
  --muted:    #5e7490;   /* secondary labels */
  --spend-r:  #b52b2b;   /* cost increases, spikes */
  --save-g:   #177840;   /* savings, RI coverage, decreases */
  --warn-o:   #b05810;   /* on-demand, warnings */
  --mono: ui-monospace, "SF Mono", "Cascadia Code", Consolas, monospace;
}

*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

html { scroll-behavior: smooth; }

body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, Arial, sans-serif;
  font-size: 12.5px; line-height: 1.55; color: var(--ink);
  background: var(--chalk);
}

.page { max-width: 1080px; margin: 0 auto; padding: 32px 40px 60px; }

/* ── Report header ── */
.rpt-header {
  background: var(--ink);
  color: #fff;
  border-radius: 6px;
  padding: 28px 32px;
  margin-bottom: 36px;
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 24px;
  align-items: start;
}
.rpt-header h1 {
  font-size: 17px; font-weight: 700; letter-spacing: -0.4px;
  margin-bottom: 16px; color: #fff;
}
.rpt-meta { display: grid; grid-template-columns: auto 1fr; gap: 5px 20px; font-size: 11px; }
.rpt-meta .k { color: #7bafd4; font-weight: 600; letter-spacing: 0.05em; text-transform: uppercase; font-size: 10px; }
.rpt-meta .v { color: #c8ddf0; font-variant-numeric: tabular-nums; }

/* ── Scroll-spy TOC (sidebar ≥1520px, collapsible on smaller screens) ── */
.toc {
  display: none;
  position: fixed;
  top: 32px;
  left: calc(50% - 540px - 230px);
  width: 210px;
  max-height: calc(100vh - 64px);
  overflow-y: auto;
  font-size: 11px;
}
@media (min-width: 1520px) { .toc { display: block; } }
.toc-hdr {
  font-size: 9.5px; font-weight: 800; text-transform: uppercase;
  letter-spacing: 0.12em; color: var(--muted);
  padding: 0 10px 6px; border-bottom: 1px solid var(--rule); margin-bottom: 4px;
}
.toc a {
  display: block;
  padding: 4px 10px;
  color: var(--muted);
  text-decoration: none;
  border-left: 2px solid transparent;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  border-radius: 0 3px 3px 0;
}
.toc a:hover { color: var(--ink); background: var(--card); }
.toc a.active {
  color: var(--lead); font-weight: 700;
  border-left-color: var(--lead);
  background: var(--card);
}
.toc .toc-num {
  display: inline-block; min-width: 22px;
  font-family: var(--mono); font-size: 9.5px; color: var(--muted);
}
.toc a.active .toc-num { color: var(--lead); }

.toc-mobile {
  margin-bottom: 20px;
  background: var(--card);
  border: 1px solid var(--rule);
  border-radius: 5px;
}
@media (min-width: 1520px) { .toc-mobile { display: none; } }
.toc-mobile summary {
  padding: 10px 14px; cursor: pointer;
  font-size: 11px; font-weight: 700; text-transform: uppercase;
  letter-spacing: 0.08em; color: var(--muted);
  list-style: none;
}
.toc-mobile summary::before { content: "☰  "; }
.toc-mobile[open] summary { border-bottom: 1px solid var(--rule); }
.toc-mobile a {
  display: block; padding: 6px 16px;
  font-size: 12px; color: var(--ink); text-decoration: none;
}
.toc-mobile a:active, .toc-mobile a:hover { background: var(--chalk); }
.toc-mobile .toc-num {
  display: inline-block; min-width: 26px;
  font-family: var(--mono); font-size: 10px; color: var(--muted);
}

/* ── Section headers ── */
h2 {
  font-size: 11px;
  font-weight: 700;
  color: var(--ink);
  text-transform: uppercase;
  letter-spacing: 0.08em;
  border-left: 3px solid var(--lead);
  padding: 8px 14px;
  margin: 36px 0 10px;
  background: var(--card);
  display: flex;
  align-items: center;
  gap: 10px;
  border-bottom: 1px solid var(--rule);
  scroll-margin-top: 16px;
}
h2 .section-num {
  background: var(--lead);
  color: #fff;
  font-size: 9.5px;
  font-weight: 700;
  padding: 2px 8px;
  border-radius: 3px;
  letter-spacing: 0.03em;
  flex-shrink: 0;
}

/* ── Tables ── */
.tbl-wrap { overflow-x: auto; margin-bottom: 18px; background: var(--card); }
table { border-collapse: collapse; width: 100%; font-size: 11.5px; font-variant-numeric: tabular-nums; }
thead tr { border-bottom: 2px solid var(--lead); }
thead th {
  background: #1e3d5f; color: #dce8f5;
  padding: 9px 14px; text-align: left; font-weight: 600;
  white-space: nowrap; font-size: 10.5px;
  letter-spacing: 0.06em; text-transform: uppercase;
}
thead th.r { text-align: right; }
tbody tr { border-bottom: 1px solid var(--rule); }
tbody tr:last-child { border-bottom: none; }
tbody tr:hover { background: #e8f0f8; }
tbody td {
  padding: 7px 14px;
  white-space: nowrap;
  color: var(--ink);
}
tfoot td {
  padding: 8px 14px;
  background: #dce6f2;
  font-weight: 700;
  border-top: 2px solid var(--lead);
  white-space: nowrap;
  color: var(--ink);
}
/* cell types */
td.num  { text-align: right; font-family: var(--mono); font-size: 11px; }
td.pos  { text-align: right; font-family: var(--mono); font-size: 11px; color: var(--spend-r); font-weight: 600; }
td.neg  { text-align: right; font-family: var(--mono); font-size: 11px; color: var(--save-g);  font-weight: 600; }
td.ri   { color: var(--save-g); font-weight: 700; }
td.od   { color: var(--warn-o); font-weight: 600; }
td.flag     { color: var(--spend-r); font-weight: 700; }
td.num-spike { text-align: right; font-family: var(--mono); font-size: 11px; color: var(--spend-r); font-weight: 700; }
td.chg  { color: var(--warn-o); font-weight: 600; }
td.mono { font-family: var(--mono); font-size: 11px; }
td.dim  { color: var(--muted); }

/* ── Bar chart ── */
.barchart {
  background: var(--card);
  border: 1px solid var(--rule);
  padding: 14px 18px;
  margin-bottom: 18px;
  font-size: 11px;
}
.barchart-row {
  display: grid;
  grid-template-columns: 104px 100px 1fr 180px;
  align-items: center;
  gap: 10px;
  padding: 4px 2px;
  border-radius: 2px;
}
.barchart-row:hover { background: #e8f0f8; }
.bar-date { font-family: var(--mono); color: var(--muted); font-size: 10.5px; }
.bar-amt  { font-family: var(--mono); text-align: right; font-weight: 600; font-size: 11px; color: var(--ink); }
.bar-track { background: #dce6f2; height: 12px; overflow: hidden; }
.bar-fill  { height: 100%; background: var(--lead); }
.bar-fill.spike { background: var(--spend-r); }
.bar-fill.hi    { background: var(--warn-o); }
.bar-label { font-size: 10px; color: var(--spend-r); font-weight: 700; white-space: nowrap; }

/* ── Pre blocks ── */
pre {
  background: #f6f8fb;
  border: 1px solid var(--rule);
  padding: 13px 16px;
  font-family: var(--mono);
  font-size: 10.5px;
  white-space: pre-wrap;
  word-break: break-word;
  line-height: 1.6;
  margin-bottom: 16px;
  overflow-x: auto;
}
pre .spike { color: var(--spend-r); font-weight: 700; }
pre .chg   { color: var(--warn-o);  font-weight: 700; }
pre .ri    { color: var(--save-g);  font-weight: 700; }
pre .muted { color: var(--muted); }

/* ── Notes & prose ── */
.note {
  color: var(--muted);
  font-size: 10.5px;
  font-style: italic;
  margin: -10px 0 14px 2px;
}
.prose {
  font-size: 11.5px;
  color: var(--ink);
  margin: 0 0 10px 2px;
  line-height: 1.5;
}

/* ── Findings / recommendation cards ── */
.findings { display: flex; flex-direction: column; gap: 8px; margin: 6px 0 22px; }
.finding-group-hdr {
  font-size: 9.5px; font-weight: 800; text-transform: uppercase;
  letter-spacing: 0.12em; color: var(--muted);
  margin: 14px 0 4px; padding-bottom: 5px;
  border-bottom: 1px solid var(--rule);
}
.finding-group-hdr:first-child { margin-top: 0; }
.finding-card {
  background: var(--card);
  border: 1px solid var(--rule);
  border-left: 4px solid var(--rule);
  border-radius: 4px;
  overflow: hidden;
}
.finding-card.pri-high   { border-left-color: #c0392b; }
.finding-card.pri-medium { border-left-color: #d4821a; }
.finding-card.pri-low    { border-left-color: #2471a3; }
.finding-card.pri-info   { border-left-color: var(--muted); }
.finding-meta {
  display: flex; align-items: center; gap: 10px;
  padding: 5px 11px; background: #f5f7fb;
  border-bottom: 1px solid var(--rule);
}
.finding-badge {
  font-size: 9px; font-weight: 800; letter-spacing: 0.1em;
  padding: 2px 7px; border-radius: 3px; color: #fff; flex-shrink: 0;
}
.badge-high   { background: #c0392b; }
.badge-medium { background: #d4821a; }
.badge-low    { background: #2471a3; }
.badge-info   { background: var(--muted); }
.finding-cat { font-size: 10px; color: var(--muted); font-weight: 600; }
.finding-save {
  margin-left: auto;
  font-size: 11px; font-weight: 700; color: var(--save-g); font-family: var(--mono);
}
.finding-text { padding: 9px 13px; font-size: 12px; color: var(--ink); line-height: 1.5; }
pre .action-tag { color: var(--warn-o); font-weight: 700; }
/* Savings / RI plan tables — green-tinted header */
.action-table thead th { background: #0d4422; }
.action-table thead tr { border-bottom-color: var(--save-g); }
.action-table tbody tr:nth-child(odd) { background: #f5fcf8; }

/* ── Key-value stat tables ── */
.kv-table td:first-child {
  color: var(--muted); font-weight: 600; width: 360px;
}
.kv-table td:last-child { font-family: var(--mono); font-size: 11px; }

/* ── Token lists (bucket names, etc.) ── */
.token-list {
  columns: 2; column-gap: 32px;
  list-style: none;
  margin: 0 0 16px 2px;
  font-family: var(--mono); font-size: 10.5px;
}
.token-list li {
  padding: 2px 0 2px 12px; color: var(--ink);
  position: relative;
  break-inside: avoid;
}
.token-list li::before {
  content: "•"; position: absolute; left: 0; color: var(--muted);
}

/* ── Prose risk/warn markers ── */
.prose .risk { color: var(--spend-r); font-weight: 700; }
.prose .warn { color: var(--warn-o); font-weight: 700; }

/* ── KPI summary row ── */
.kpi-row {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
  margin: 0 0 24px;
}
.kpi-card {
  background: var(--card);
  border: 1px solid var(--rule);
  border-radius: 6px;
  padding: 16px 20px;
}
.kpi-label {
  font-size: 9.5px; font-weight: 700; text-transform: uppercase;
  letter-spacing: 0.1em; color: var(--muted); margin-bottom: 6px;
}
.kpi-value {
  font-size: 22px; font-weight: 700; font-family: var(--mono);
  font-variant-numeric: tabular-nums; color: var(--ink);
  letter-spacing: -0.5px; line-height: 1.2; margin-bottom: 4px;
}
.kpi-sub { font-size: 10.5px; color: var(--muted); }
.kpi-up .kpi-value    { color: var(--spend-r); }
.kpi-down .kpi-value  { color: var(--save-g); }

/* ── Auto-insights block ── */
.insights {
  background: #edf3fb;
  border-left: 3px solid var(--lead);
  border-radius: 0 4px 4px 0;
  padding: 12px 16px 12px 14px;
  margin: 0 0 28px;
}
.insights-hdr {
  font-size: 9.5px; font-weight: 700; text-transform: uppercase;
  letter-spacing: 0.1em; color: var(--lead); margin-bottom: 8px;
}
.insight-item {
  font-size: 11.5px; color: var(--ink); line-height: 1.7;
}
.insight-item::before { content: "→  "; color: var(--lead); font-weight: 700; }

/* ── Charts dashboard ── */
.charts-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
  margin-bottom: 36px;
}
.chart-card {
  background: var(--card);
  border: 1px solid var(--rule);
  padding: 16px 18px;
}
.chart-card.span2 { grid-column: 1 / -1; }
.chart-lbl {
  font-size: 10px; font-weight: 700; letter-spacing: 0.08em;
  text-transform: uppercase; color: var(--muted); margin-bottom: 12px;
}
.chart-wrap { position: relative; height: 180px; }
.chart-wrap.h280 { height: 280px; }

/* ── Mobile ── */
@media (max-width: 760px) {
  .page { padding: 14px 12px 40px; }
  .rpt-header {
    grid-template-columns: 1fr;
    padding: 18px 18px;
    gap: 12px;
    margin-bottom: 20px;
  }
  .rpt-header h1 { font-size: 15px; margin-bottom: 10px; }
  .rpt-meta { font-size: 10.5px; gap: 4px 12px; }
  h2 { margin: 26px 0 10px; white-space: normal; }
  .kpi-row { grid-template-columns: 1fr 1fr; gap: 8px; }
  .kpi-card { padding: 12px 14px; }
  .kpi-value { font-size: 18px; }
  .charts-grid { grid-template-columns: 1fr; gap: 12px; }
  .chart-card.span2 { grid-column: auto; }
  .chart-wrap { height: 200px; }
  .token-list { columns: 1; }
  .kv-table td:first-child { width: auto; min-width: 130px; }
  .barchart { padding: 10px 10px; }
  .barchart-row { grid-template-columns: 74px 70px 1fr; gap: 6px; }
  .bar-date { font-size: 9.5px; }
  .bar-amt  { font-size: 10px; }
  .bar-label {
    grid-column: 1 / -1;
    padding-left: 80px;
    margin-top: -2px;
  }
  .finding-meta { flex-wrap: wrap; row-gap: 4px; }
  .finding-save { margin-left: 0; width: 100%; }
  table { font-size: 10.5px; }
  thead th { padding: 7px 9px; font-size: 9.5px; }
  tbody td, tfoot td { padding: 6px 9px; }
  pre { font-size: 9.5px; padding: 10px 12px; }
}

/* ── Print ── */
@page {
  size: A4 landscape;
  margin: 10mm 14mm;
}
@media print {
  body { background: #fff; font-size: 9.5px; }
  .page { padding: 0; max-width: none; }
  .toc, .toc-mobile { display: none !important; }
  h2 {
    font-size: 9px; padding: 5px 10px; margin: 18px 0 6px;
    break-after: avoid;
  }
  h2 .section-num { font-size: 8px; padding: 1px 6px; }
  .rpt-header { padding: 14px 18px; margin-bottom: 18px; border-radius: 3px; }
  .rpt-header h1 { font-size: 13px; margin-bottom: 8px; }
  .rpt-meta { font-size: 9px; gap: 3px 12px; }
  /* Tables */
  .tbl-wrap { margin-bottom: 10px; overflow: visible; }
  table { font-size: 9px; }
  thead th { padding: 5px 8px; font-size: 8.5px; }
  tbody td, tfoot td { padding: 4px 8px; }
  /* Wide tables — scale down further so all columns fit */
  .tbl-wide table { font-size: 8px; }
  .tbl-wide thead th { padding: 4px 6px; }
  .tbl-wide tbody td, .tbl-wide tfoot td { padding: 3px 6px; }
  /* Pre */
  pre { font-size: 8.5px; padding: 8px 10px; margin-bottom: 10px; }
  /* Bar chart */
  .barchart { padding: 8px 12px; margin-bottom: 10px; font-size: 9px; }
  .barchart-row { grid-template-columns: 90px 86px 1fr 150px; gap: 6px; padding: 2px; }
  /* Page breaks */
  table, pre, .barchart, .charts-grid { break-inside: avoid; }
  /* Charts */
  .charts-grid { gap: 10px; margin-bottom: 20px; }
  .chart-card { padding: 10px 12px; }
  .chart-wrap { height: 140px; }
  .chart-wrap.h280 { height: 210px; }
  thead th {
    background: var(--ink) !important;
    -webkit-print-color-adjust: exact;
    print-color-adjust: exact;
  }
  .rpt-header {
    background: var(--ink) !important;
    -webkit-print-color-adjust: exact;
    print-color-adjust: exact;
  }
  tfoot td {
    background: #dce6f2 !important;
    -webkit-print-color-adjust: exact;
    print-color-adjust: exact;
  }
}
"""

# ─── Chart.js — download once, cache locally, inline in HTML ─────────────────

_CHARTJS_CACHE = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), ".chartjs.min.js")
_CHARTJS_CDN   = "https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js"

_SVC_SHORT = {
    "Amazon Elastic Compute Cloud - Compute":      "EC2 Compute",
    "Amazon Relational Database Service":          "RDS",
    "AmazonCloudWatch":                            "CloudWatch",
    "Amazon Elastic Container Service":            "ECS",
    "AmazonCloudFront":                            "CloudFront",
    "Amazon Virtual Private Cloud":                "VPC",
    "Amazon Simple Storage Service":               "S3",
    "AWS Lambda":                                  "Lambda",
    "Amazon Elastic Load Balancing":               "ELB",
    "EC2 - Other":                                 "EC2 Other",
    "Amazon ElastiCache":                          "ElastiCache",
    "Savings Plans for AWS Compute usage":         "Savings Plans",
    "AWS WAF":                                     "WAF",
    "AWS Key Management Service":                  "KMS",
    "Amazon Route 53":                             "Route 53",
    "Amazon Elastic Kubernetes Service":           "EKS",
    "Tax":                                         "Tax",
}

def _short_svc(name):
    return _SVC_SHORT.get(name, name[:32] if len(name) > 32 else name)


def _get_chartjs():
    """Return (mode, payload). mode='inline': payload is JS text. mode='cdn': payload is URL."""
    if _os.path.exists(_CHARTJS_CACHE):
        try:
            with open(_CHARTJS_CACHE) as f:
                return "inline", f.read()
        except Exception:
            pass
    try:
        sys.stderr.write("  Downloading Chart.js (one-time, ~200 KB)...\n")
        data = _urlreq.urlopen(_CHARTJS_CDN, timeout=15).read().decode()
        with open(_CHARTJS_CACHE, "w") as f:
            f.write(data)
        sys.stderr.write(f"  ✓ Cached to {_os.path.basename(_CHARTJS_CACHE)}\n")
        return "inline", data
    except Exception as exc:
        sys.stderr.write(f"  [WARN] Chart.js unavailable ({exc}) — charts will need internet\n")
        return "cdn", _CHARTJS_CDN


def _load_chart_data(data_dir):
    if not data_dir or not _os.path.isdir(data_dir):
        return {}

    def _load(rel):
        p = _os.path.join(data_dir, rel)
        if not _os.path.exists(p):
            return None
        try:
            with open(p) as f:
                return _json.load(f)
        except Exception:
            return None

    out = {}

    # Monthly totals
    d = _load("billing/monthly_total.json")
    if d and d.get("ResultsByTime"):
        rows = d["ResultsByTime"]
        out["monthly"] = {
            "labels":  [r["TimePeriod"]["Start"][:7] for r in rows],
            "amounts": [round(float(r["Total"]["BlendedCost"]["Amount"]), 2) for r in rows],
        }

    # Daily totals (last 30 days)
    d = _load("billing/daily_total.json")
    if d and d.get("ResultsByTime"):
        rows = d["ResultsByTime"]
        out["daily"] = {
            "labels":  [r["TimePeriod"]["Start"] for r in rows],
            "amounts": [round(float(r["Total"]["BlendedCost"]["Amount"]), 2) for r in rows],
        }

    # Daily RDS by record type — stacked bar
    d = _load("billing/daily_rds_by_record_type.json")
    if d and d.get("ResultsByTime"):
        rows = d["ResultsByTime"]
        dates = [r["TimePeriod"]["Start"] for r in rows]
        buckets = ["DiscountedUsage", "Usage", "Recurring"]
        type_data = {b: [] for b in buckets}
        for r in rows:
            day = {g["Keys"][0]: round(float(g["Metrics"]["BlendedCost"]["Amount"]), 2)
                   for g in r.get("Groups", [])}
            for b in buckets:
                type_data[b].append(day.get(b, 0))
        out["rds_daily"] = {"labels": dates, "types": type_data}

    # Top services — latest complete month (starts on the 1st)
    d = _load("billing/monthly_by_service.json")
    if d and d.get("ResultsByTime"):
        rows = d["ResultsByTime"]
        complete = [r for r in rows if r["TimePeriod"]["Start"].endswith("-01")]
        period = complete[-1] if complete else rows[-1]
        groups = period.get("Groups", [])
        svc = [(_short_svc(g["Keys"][0]), float(g["Metrics"]["BlendedCost"]["Amount"]))
               for g in groups if float(g["Metrics"]["BlendedCost"]["Amount"]) > 1]
        svc.sort(key=lambda x: x[1], reverse=True)
        if svc:
            out["services"] = {
                "month":   period["TimePeriod"]["Start"][:7],
                "labels":  [s for s, _ in svc[:10]],
                "amounts": [round(a, 2) for _, a in svc[:10]],
            }

    return out


def _render_charts(charts):
    if not charts:
        return ""

    cjsmode, cjspayload = _get_chartjs()
    if cjsmode == "inline":
        cjs_tag = f"<script>{cjspayload}</script>"
    else:
        cjs_tag = f'<script src="{cjspayload}"></script>'

    panels = []
    inits  = []

    # ── Monthly bar ──────────────────────────────────────────────────────────
    if "monthly" in charts:
        m = charts["monthly"]
        lj = _json.dumps(m["labels"])
        aj = _json.dumps(m["amounts"])
        panels.append(
            '<div class="chart-card">'
            '<div class="chart-lbl">Monthly Cost</div>'
            '<div class="chart-wrap"><canvas id="cMo"></canvas></div>'
            '</div>'
        )
        inits.append(f"""new Chart(document.getElementById('cMo'),{{
  type:'bar',
  data:{{labels:{lj},datasets:[{{
    data:{aj},backgroundColor:'#1a5fa8',borderRadius:3,borderSkipped:false
  }}]}},
  options:{{
    responsive:true,maintainAspectRatio:false,
    plugins:{{legend:{{display:false}},tooltip:{{callbacks:{{label:c=>'$'+c.raw.toLocaleString()}}}}}},
    scales:{{
      y:{{ticks:{{callback:v=>'$'+(v>=1000?(v/1000).toFixed(0)+'k':v),font:{{size:10}}}},grid:{{color:'#e0e8f2'}}}},
      x:{{ticks:{{font:{{size:10}}}},grid:{{display:false}}}}
    }}
  }}
}});""")

    # ── Daily line ───────────────────────────────────────────────────────────
    if "daily" in charts:
        d = charts["daily"]
        lj = _json.dumps(d["labels"])
        aj = _json.dumps(d["amounts"])
        panels.append(
            '<div class="chart-card">'
            '<div class="chart-lbl">Daily Cost — Last 30 Days</div>'
            '<div class="chart-wrap"><canvas id="cDay"></canvas></div>'
            '</div>'
        )
        inits.append(f"""new Chart(document.getElementById('cDay'),{{
  type:'line',
  data:{{labels:{lj},datasets:[{{
    data:{aj},borderColor:'#1a5fa8',backgroundColor:'rgba(26,95,168,0.08)',
    fill:true,tension:0.3,pointRadius:2,pointHoverRadius:5,borderWidth:2
  }}]}},
  options:{{
    responsive:true,maintainAspectRatio:false,
    plugins:{{legend:{{display:false}},tooltip:{{callbacks:{{label:c=>'$'+c.raw.toLocaleString()}}}}}},
    scales:{{
      y:{{ticks:{{callback:v=>'$'+(v>=1000?(v/1000).toFixed(0)+'k':v),font:{{size:10}}}},grid:{{color:'#e0e8f2'}}}},
      x:{{ticks:{{font:{{size:9}},maxTicksLimit:10,maxRotation:45}},grid:{{display:false}}}}
    }}
  }}
}});""")

    # ── Daily RDS stacked bar ────────────────────────────────────────────────
    if "rds_daily" in charts:
        rd   = charts["rds_daily"]
        lj   = _json.dumps(rd["labels"])
        disc = _json.dumps(rd["types"]["DiscountedUsage"])
        od   = _json.dumps(rd["types"]["Usage"])
        rec  = _json.dumps(rd["types"]["Recurring"])
        panels.append(
            '<div class="chart-card span2">'
            '<div class="chart-lbl">Daily RDS Billing — Last 30 Days</div>'
            '<div class="chart-wrap"><canvas id="cRds"></canvas></div>'
            '</div>'
        )
        inits.append(f"""new Chart(document.getElementById('cRds'),{{
  type:'bar',
  data:{{labels:{lj},datasets:[
    {{label:'RI Covered',  data:{disc},backgroundColor:'#1a5fa8',stack:'s'}},
    {{label:'On-Demand',   data:{od},  backgroundColor:'#b05810',stack:'s'}},
    {{label:'RI Monthly Fee',data:{rec},backgroundColor:'#b52b2b',stack:'s'}}
  ]}},
  options:{{
    responsive:true,maintainAspectRatio:false,
    plugins:{{
      legend:{{position:'bottom',labels:{{font:{{size:10}},boxWidth:12,padding:14}}}},
      tooltip:{{mode:'index',intersect:false,callbacks:{{label:c=>'  '+c.dataset.label+': $'+c.raw.toLocaleString()}}}}
    }},
    scales:{{
      y:{{stacked:true,ticks:{{callback:v=>'$'+(v>=1000?(v/1000).toFixed(0)+'k':v),font:{{size:10}}}},grid:{{color:'#e0e8f2'}}}},
      x:{{stacked:true,ticks:{{font:{{size:9}},maxTicksLimit:15,maxRotation:45}},grid:{{display:false}}}}
    }}
  }}
}});""")

    # ── Services horizontal bar ──────────────────────────────────────────────
    if "services" in charts:
        s = charts["services"]
        lj = _json.dumps(s["labels"])
        aj = _json.dumps(s["amounts"])
        n  = len(s["labels"])
        colors = _json.dumps(
            ["#b52b2b" if i == 0 else ("#1a5fa8" if i < 3 else "#5e7490") for i in range(n)]
        )
        panels.append(
            f'<div class="chart-card span2">'
            f'<div class="chart-lbl">Top Services — {s["month"]}</div>'
            f'<div class="chart-wrap h280"><canvas id="cSvc"></canvas></div>'
            f'</div>'
        )
        inits.append(f"""new Chart(document.getElementById('cSvc'),{{
  type:'bar',
  data:{{labels:{lj},datasets:[{{
    data:{aj},backgroundColor:{colors},borderRadius:3,borderSkipped:false
  }}]}},
  options:{{
    indexAxis:'y',
    responsive:true,maintainAspectRatio:false,
    plugins:{{legend:{{display:false}},tooltip:{{callbacks:{{label:c=>'$'+c.raw.toLocaleString()}}}}}},
    scales:{{
      x:{{ticks:{{callback:v=>'$'+(v>=1000?(v/1000).toFixed(0)+'k':v),font:{{size:10}}}},grid:{{color:'#e0e8f2'}}}},
      y:{{ticks:{{font:{{size:10}}}},grid:{{display:false}}}}
    }}
  }}
}});""")

    if not panels:
        return ""

    inits_js = "\n".join(inits)
    return (
        f"{cjs_tag}\n"
        f'<div class="charts-grid">{"".join(panels)}</div>\n'
        f"<script>document.addEventListener('DOMContentLoaded',function(){{"
        f"{inits_js}}});</script>"
    )


def _render_section1_kpis(charts):
    m = charts.get("monthly", {})
    d = charts.get("daily", {})
    cards = []

    # Latest monthly total
    if m.get("labels") and m.get("amounts"):
        last_label = m["labels"][-1]
        last_amt   = m["amounts"][-1]
        cards.append(
            f'<div class="kpi-card">'
            f'<div class="kpi-label">Latest Month</div>'
            f'<div class="kpi-value">${last_amt:,.0f}</div>'
            f'<div class="kpi-sub">{last_label}</div>'
            f'</div>'
        )

    # MoM change
    if m.get("amounts") and len(m["amounts"]) >= 2:
        prev = m["amounts"][-2]
        curr = m["amounts"][-1]
        if prev > 0:
            pct = (curr - prev) / prev * 100
            cls  = "kpi-up" if pct > 5 else ("kpi-down" if pct < -5 else "")
            sign = "+" if pct > 0 else ""
            cards.append(
                f'<div class="kpi-card {cls}">'
                f'<div class="kpi-label">MoM Change</div>'
                f'<div class="kpi-value">{sign}{pct:.1f}%</div>'
                f'<div class="kpi-sub">{m["labels"][-2]} → {m["labels"][-1]}</div>'
                f'</div>'
            )

    # Daily avg excluding month-start spikes
    if d.get("amounts") and d.get("labels") and len(d["amounts"]) > 3:
        non_spike = [a for a, l in zip(d["amounts"], d["labels"]) if not l.endswith("-01")]
        if non_spike:
            avg = sum(non_spike) / len(non_spike)
            cards.append(
                f'<div class="kpi-card">'
                f'<div class="kpi-label">Daily Avg</div>'
                f'<div class="kpi-value">${avg:,.0f}</div>'
                f'<div class="kpi-sub">excl. month-start spikes</div>'
                f'</div>'
            )

    # Highest single day
    if d.get("amounts") and d.get("labels"):
        max_idx = d["amounts"].index(max(d["amounts"]))
        cards.append(
            f'<div class="kpi-card kpi-up">'
            f'<div class="kpi-label">Highest Day</div>'
            f'<div class="kpi-value">${d["amounts"][max_idx]:,.0f}</div>'
            f'<div class="kpi-sub">{d["labels"][max_idx]}</div>'
            f'</div>'
        )

    if not cards:
        return ""
    return '<div class="kpi-row">' + ''.join(cards) + '</div>'


def _render_section1_insights(charts):
    m = charts.get("monthly", {})
    d = charts.get("daily", {})
    bullets = []

    # Trend over all months
    if m.get("amounts") and len(m["amounts"]) >= 2:
        first, last = m["amounts"][0], m["amounts"][-1]
        if first > 0:
            total_pct = (last - first) / first * 100
            n = len(m["amounts"])
            direction = "up" if total_pct > 0 else "down"
            bullets.append(
                f"Spend {direction} {abs(total_pct):.0f}% over {n} months "
                f"({m['labels'][0]} → {m['labels'][-1]}): "
                f"${first:,.0f} → ${last:,.0f}/month"
            )

    # Largest single MoM swing
    if m.get("amounts") and len(m["amounts"]) >= 2:
        amts   = m["amounts"]
        labels = m["labels"]
        changes = [(amts[i] - amts[i-1], i) for i in range(1, len(amts))]
        biggest_change, biggest_i = max(changes, key=lambda x: abs(x[0]))
        if abs(biggest_change) > 500:
            direction = "increase" if biggest_change > 0 else "decrease"
            bullets.append(
                f"Largest MoM {direction}: ${abs(biggest_change):,.0f} "
                f"({labels[biggest_i-1]} → {labels[biggest_i]})"
            )

    # Month-start spikes
    if d.get("amounts") and d.get("labels"):
        spikes = [(l, a) for l, a in zip(d["labels"], d["amounts"])
                  if l.endswith("-01") and a > 1000]
        if spikes:
            spike_str = ", ".join(f"{l}: ${a:,.0f}" for l, a in spikes[:3])
            bullets.append(
                f"Month-start billing spikes (RI fees + on-demand lump): {spike_str}"
            )

    if not bullets:
        return ""
    items_html = "".join(
        f'<div class="insight-item">{H.escape(b)}</div>' for b in bullets
    )
    return (
        '<div class="insights">'
        '<div class="insights-hdr">Key Insights</div>'
        f'{items_html}'
        '</div>'
    )


# ─── Helpers ──────────────────────────────────────────────────────────────────

def esc(s): return H.escape(str(s))

SEP_RE   = re.compile(r'^[─ ]+$')
SEC_RE   = re.compile(r'^─{3} (.+?)(?:\s+─+)?\s*$')
BORD_RE  = re.compile(r'^[═]+\s*$')

def is_sep(line):
    s = line.strip()
    return bool(s) and len(s) > 3 and SEP_RE.match(s) is not None

def is_section(line):
    return SEC_RE.match(line.strip()) is not None

def is_border(line):
    return BORD_RE.match(line.strip()) is not None

# ─── Column extraction from separator line ────────────────────────────────────

def sep_bounds(sep_line):
    """Return [(start, end), ...] for each ─ run in sep_line."""
    cols, in_d, s = [], False, 0
    for i, c in enumerate(sep_line):
        if c == '─':
            if not in_d: s = i; in_d = True
        elif in_d:
            cols.append((s, i)); in_d = False
    if in_d:
        cols.append((s, len(sep_line)))
    return cols

def extract_row(line, bounds):
    """Slice a row into cells using column boundaries."""
    cells = []
    for idx, (s, _) in enumerate(bounds):
        if s >= len(line):
            cells.append('')
            continue
        if idx + 1 < len(bounds):
            end = bounds[idx + 1][0]          # up to next col start
        else:
            end = len(line)                   # last col: rest of line
        cells.append(line[s:end].strip())
    return cells

# ─── Cell classifier ──────────────────────────────────────────────────────────

def cell_cls(val, col_idx, header=None):  # noqa: ARG001
    v = val.strip()
    if col_idx == 0: return ''
    if v in ('✓ RI', '✓', '100% RI', 'RI', '67% RI', '50% RI'):
        return 'ri'
    if v in ('on-demand', 'OD', 'on demand'):
        return 'od'
    if ('←' in v or 'spike' in v.lower()) and re.match(r'^\$', v):
        return 'num-spike'   # dollar amount with spike label — keep right-aligned, colour red
    if '←' in v or 'spike' in v.lower():
        return 'flag'
    if re.match(r'^\+~?\$', v):
        return 'pos'
    if v.startswith('+') and '%' in v:
        return 'pos'
    if v.startswith('-') and '%' in v and not v.startswith('- '):
        return 'neg'
    if re.match(r'^~?\$[\d\s,.$]+', v):
        return 'num'
    if re.match(r'^\$\s*[\d,]+', v):
        return 'num'
    if '↓' in v or '→' in v:
        return 'chg'
    if re.match(r'^[\d,]+\.?\d*$', v.replace(',', '')):
        return 'num'
    return ''

def is_right(cls): return cls in ('num', 'pos', 'neg', 'num-spike')

# ─── Table renderer ───────────────────────────────────────────────────────────

def render_table(header_line, sep_line, data_lines):
    bounds = sep_bounds(sep_line)
    if not bounds:
        return render_pre([header_line, sep_line] + data_lines)

    headers = extract_row(header_line, bounds)
    rows, tfoot = [], None
    past_second_sep = False

    for line in data_lines:
        if not line.strip():
            continue
        if is_sep(line):
            past_second_sep = True
            continue
        if past_second_sep and not tfoot:
            tfoot = extract_row(line, bounds)
            past_second_sep = False
            continue
        cells = extract_row(line, bounds)
        if any(c for c in cells):
            rows.append(cells)

    # Determine per-column alignment from data — header follows data, not the other way round
    ncols = max(len(headers), max((len(r) for r in rows), default=0))
    col_right = []
    for i in range(ncols):
        if i == 0:
            col_right.append(False)
            continue
        h = headers[i] if i < len(headers) else ''
        right_count = sum(1 for r in rows if i < len(r) and is_right(cell_cls(r[i], i, h)))
        col_right.append(right_count > len(rows) / 2 if rows else False)

    wide_cls   = ' tbl-wide' if len(headers) >= 6 else ''
    action_cls = ' action-table' if any(
        kw in h.lower() for h in headers for kw in ('saving', 'recommended action')
    ) else ''
    out = [f'<div class="tbl-wrap{wide_cls}{action_cls}"><table>']

    # thead — align header to match column data
    out.append('<thead><tr>')
    for i, h in enumerate(headers):
        r_cls = ' class="r"' if (i < len(col_right) and col_right[i]) else ''
        out.append(f'<th{r_cls}>{esc(h)}</th>')
    out.append('</tr></thead>')

    # tbody
    out.append('<tbody>')
    for cells in rows:
        out.append('<tr>')
        padded = cells + [''] * max(0, ncols - len(cells))
        for i, c in enumerate(padded[:ncols]):
            h = headers[i] if i < len(headers) else ''
            cls = cell_cls(c, i, h)
            attr = f' class="{cls}"' if cls else ''
            out.append(f'<td{attr}>{esc(c)}</td>')
        out.append('</tr>')
    out.append('</tbody>')

    # tfoot
    if tfoot:
        out.append('<tfoot><tr>')
        padded = tfoot + [''] * max(0, ncols - len(tfoot))
        for i, c in enumerate(padded[:ncols]):
            h = headers[i] if i < len(headers) else ''
            cls = cell_cls(c, i, h)
            attr = f' class="{cls}"' if cls else ''
            out.append(f'<td{attr}>{esc(c)}</td>')
        out.append('</tr></tfoot>')

    out.append('</table></div>')
    return ''.join(out)

# ─── Bar chart renderer ───────────────────────────────────────────────────────

BAR_RE   = re.compile(r'^\s*(\d{4}-\d{2}-\d{2})\s+\$\s*([\d,]+\.\d+)\s*(█*)(.*)?$')
_MM_RE   = re.compile(r'^\s+(\d{4}-\d{2})\s+\$\s*([\d,]+\.\d+)\s*(.*)')
_KV_RE   = re.compile(r'^\s+(.{1,60}?)\s*:\s+(\S.*)$')
_LABEL_RE = re.compile(r'^\s+\S.{0,78}:\s*$|^\s+\[[^\]]+\]\s*$')

def _kv_match(line):
    """Key-value stat line: 'CPU   : 157,184 units' or 'NAT cost: $563.04'.
    Requires alignment spaces before the colon, or a numeric/dollar value —
    this keeps prose like 'Diagnosis: RDS ...' out."""
    m = _KV_RE.match(line)
    if not m:
        return None
    key, val = m.group(1), m.group(2)
    if re.search(r'\s:', line) or re.match(r'[~$\d(]', val):
        return key.strip(), val.strip()
    return None

def _split_cells(line):
    s = re.sub(r'\$\s+', '$', line.strip())
    return re.split(r'\s{2,}', s)

def _row_cells(line):
    """Aligned columns split on 2+ spaces — None unless it yields 2+ cells."""
    if not line.strip() or is_sep(line):
        return None
    cells = _split_cells(line)
    return cells if len(cells) >= 2 else None

def _is_label(line):
    return bool(_LABEL_RE.match(line))

def _is_token(line):
    s = line.strip()
    return bool(s) and ' ' not in s and not is_sep(line)

def _breaks_pre(lines, i):
    """True when line i starts a block that a dedicated handler should render —
    the prose/pre fallback must stop collecting before it."""
    l = lines[i]
    s = l.strip()
    if s.startswith('Note:') or s.startswith('Additional items:'):
        return True
    if _is_label(l):
        return True
    if BAR_RE.match(l) or _MM_RE.match(l):
        return True
    nxt = lines[i + 1] if i + 1 < len(lines) else ''
    if _kv_match(l) and _kv_match(nxt):
        return True
    if _row_cells(l) and _row_cells(nxt):
        return True
    return False
FIND_RE  = re.compile(r'^\s*\[(HIGH|MEDIUM|LOW|INFO)\|(COST|CLEANUP|PLANNING|RELIABILITY)\|(\d+)\] (.*)')
_CAT_LABEL = {
    "COST":        "💰 Cost",
    "CLEANUP":     "🗑 Cleanup",
    "PLANNING":    "📈 Planning",
    "RELIABILITY": "🔧 Reliability",
}
_CAT_ORDER = ["COST", "CLEANUP", "PLANNING", "RELIABILITY"]

def render_barchart(bar_lines):
    entries = []
    for line in bar_lines:
        m = BAR_RE.match(line)
        if m:
            date, amt_s, bars, label = m.groups()
            entries.append((date, float(amt_s.replace(',', '')), len(bars), label.strip()))

    if not entries:
        return render_pre(bar_lines)

    max_amt = max(a for _, a, _, _ in entries) or 1
    out = ['<div class="barchart">']
    for date, amt, _, label in entries:
        pct = amt / max_amt * 100
        is_spike = '←' in label or 'spike' in label.lower()
        is_hi = amt > max_amt * 0.5 and not is_spike
        fill_cls = 'spike' if is_spike else ('hi' if is_hi else '')
        cls_attr = f' {fill_cls}' if fill_cls else ''
        label_html = f'<span class="bar-label">{esc(label)}</span>' if label else ''
        out.append(
            f'<div class="barchart-row">'
            f'<span class="bar-date">{esc(date)}</span>'
            f'<span class="bar-amt">${amt:>9,.2f}</span>'
            f'<div class="bar-track"><div class="bar-fill{cls_attr}" style="width:{pct:.1f}%"></div></div>'
            f'{label_html}'
            f'</div>'
        )
    out.append('</div>')
    return ''.join(out)

# ─── Pre renderer ─────────────────────────────────────────────────────────────

def colorize(line):
    e = esc(line)
    if '← spike' in line or '← month-start spike' in line:
        e = re.sub(r'(&lt;- (?:spike|month-start spike))', r'<span class="spike">\1</span>', e)
    elif '←' in line:
        e = re.sub(r'(&lt;-[^<\n]*)', r'<span class="chg">\1</span>', e)
    if '✓ RI' in line or '✓ RI' in line:
        e = e.replace('✓ RI', '<span class="ri">✓ RI</span>')
    if re.match(r'\s*Action:', line):
        e = re.sub(r'(Action:)', r'<span class="action-tag">\1</span>', e)
    return e

def _prose_html(text):
    text = re.sub(r'\$\s+', '$', text)      # collapse alignment padding: "$    783.17"
    text = re.sub(r'\s{2,}', ' ', text)
    e = esc(text)
    e = re.sub(r'^(\[RISK\])', r'<span class="risk">\1</span>', e)
    e = re.sub(r'^(\[WARN\])', r'<span class="warn">\1</span>', e)
    return e

def render_pre(lines):
    stripped = [l.strip() for l in lines if l.strip()]
    if not stripped:
        return ''
    aligned = sum(1 for l in stripped if re.search(r'\S\s{3,}\S', l))
    if (any(re.search(r'[─│█]', l) for l in stripped)
            or len(stripped) > 12 or aligned >= 2):
        content = '\n'.join(colorize(l) for l in lines)
        return f'<pre>{content}</pre>'
    # Prose: rejoin hard-wrapped lines into paragraphs; [RISK]/[WARN] markers
    # stay as their own lines.
    paras, cur = [], []
    for l in stripped:
        if l.startswith('['):
            if cur:
                paras.append(' '.join(cur)); cur = []
            paras.append(l)
        else:
            cur.append(l)
    if cur:
        paras.append(' '.join(cur))
    return ''.join(f'<p class="prose">{_prose_html(p)}</p>' for p in paras)

# ─── Section body renderer ────────────────────────────────────────────────────

def render_body(lines):
    out = []
    i = 0
    while i < len(lines):
        # skip blank
        if not lines[i].strip():
            i += 1
            continue

        # Note line
        stripped = lines[i].strip()
        if stripped.startswith('Note:'):
            out.append(f'<p class="note">{esc(stripped)}</p>')
            i += 1
            continue

        # Table: content line followed by separator line
        if i + 1 < len(lines) and is_sep(lines[i + 1]):
            header_line = lines[i]
            sep_line    = lines[i + 1]
            i += 2
            body = []
            while i < len(lines):
                if not lines[i].strip():
                    break
                if lines[i].strip().startswith('Note:'):
                    break            # trailing note, not a table row
                body.append(lines[i])
                i += 1
            out.append(render_table(header_line, sep_line, body))
            # Trailing notes
            while i < len(lines) and lines[i].strip().startswith('Note:'):
                out.append(f'<p class="note">{esc(lines[i].strip())}</p>')
                i += 1
            continue

        # "Additional items:" — priority-tagged findings → grouped cards
        if re.match(r'\s*Additional items:', lines[i]):
            i += 1
            items = []
            while i < len(lines) and lines[i].strip():
                m = FIND_RE.match(lines[i])
                if m:
                    pri, cat, saving_s, text = m.groups()
                    items.append((pri, cat, int(saving_s), text.strip()))
                i += 1
            if items:
                grouped = {c: [] for c in _CAT_ORDER}
                for item in items:
                    grouped.setdefault(item[1], []).append(item)
                out.append('<div class="findings">')
                for cat in _CAT_ORDER:
                    cat_items = grouped.get(cat, [])
                    if not cat_items:
                        continue
                    label = _CAT_LABEL.get(cat, cat)
                    out.append(f'<div class="finding-group-hdr">{esc(label)}</div>')
                    for pri, _, saving, text in cat_items:
                        pri_lc = pri.lower()
                        save_html = (f'<span class="finding-save">${saving:,}/mo</span>'
                                     if saving > 0 else '')
                        out.append(
                            f'<div class="finding-card pri-{pri_lc}">'
                            f'<div class="finding-meta">'
                            f'<span class="finding-badge badge-{pri_lc}">{esc(pri)}</span>'
                            f'<span class="finding-cat">{esc(label)}</span>'
                            f'{save_html}'
                            f'</div>'
                            f'<div class="finding-text">{esc(text)}</div>'
                            f'</div>'
                        )
                out.append('</div>')
            continue

        # "Actual daily RDS billing" label — render as prose, then the bar chart
        # block below picks up the date+amount lines on the next iteration.
        if re.match(r'\s*Actual daily RDS billing', lines[i]):
            out.append(f'<p class="prose">{esc(lines[i].strip())}</p>')
            i += 1
            continue

        # Bar chart block: 3+ consecutive bar lines
        j = i
        bar_buf = []
        while j < len(lines) and BAR_RE.match(lines[j]):
            bar_buf.append(lines[j])
            j += 1
        if len(bar_buf) >= 3:
            out.append(render_barchart(bar_buf))
            i = j
            continue

        # YYYY-MM monthly cost block → compact table
        if _MM_RE.match(lines[i]):
            rows = []
            while i < len(lines) and _MM_RE.match(lines[i]):
                m = _MM_RE.match(lines[i])
                rows.append((m.group(1), float(m.group(2).replace(',', '')), m.group(3).strip()))
                i += 1
            out.append('<div class="tbl-wrap"><table><thead><tr>'
                       '<th>Month</th><th class="r">Cost</th><th>Note</th>'
                       '</tr></thead><tbody>')
            for mo, amt, note in rows:
                ncls = (' class="flag"' if '←' in note else
                        ' class="pos"' if '(+' in note else
                        ' class="neg"' if '(-' in note else '')
                out.append(f'<tr><td class="mono">{esc(mo)}</td>'
                           f'<td class="num">${amt:,.2f}</td>'
                           f'<td{ncls}>{esc(note)}</td></tr>')
            out.append('</tbody></table></div>')
            continue

        # "Highest single days:" block → table
        if re.match(r'\s*Highest single days:', lines[i]):
            i += 1
            _HDAY = re.compile(r'^\s+(\d{4}-\d{2}-\d{2})\s+\$\s*([\d,]+\.\d+)\s*(.*)')
            hday_rows = []
            while i < len(lines) and _HDAY.match(lines[i]):
                m = _HDAY.match(lines[i])
                hday_rows.append((m.group(1), float(m.group(2).replace(',', '')), m.group(3).strip()))
                i += 1
            if hday_rows:
                out.append('<p class="prose"><strong>Highest single days</strong></p>')
                out.append('<div class="tbl-wrap"><table><thead><tr>'
                           '<th>Date</th><th class="r">Amount</th><th>Note</th>'
                           '</tr></thead><tbody>')
                for date, amt, note in hday_rows:
                    ncls = ' class="flag"' if '←' in note else ''
                    out.append(f'<tr><td class="mono">{esc(date)}</td>'
                               f'<td class="num">${amt:,.2f}</td>'
                               f'<td{ncls}>{esc(note)}</td></tr>')
                out.append('</tbody></table></div>')
            continue

        # "Configuration changes detected" block → table
        if re.match(r'\s*Configuration changes detected', lines[i]):
            label = lines[i].strip()
            i += 1
            cfg_rows = []
            while i < len(lines) and lines[i].strip():
                m = re.match(r'\s+(\S+)\s{2,}(\S+)\s{2,}(.+?→.+)', lines[i])
                if m:
                    cfg_rows.append((m.group(1), m.group(2), m.group(3).strip()))
                i += 1
            if cfg_rows:
                out.append(f'<p class="prose">{esc(label)}</p>')
                out.append('<div class="tbl-wrap"><table><thead><tr>'
                           '<th>Snapshot</th><th>Instance</th><th>Change</th>'
                           '</tr></thead><tbody>')
                for snap, inst, chg in cfg_rows:
                    before, _, after = chg.partition('→')
                    out.append(
                        f'<tr><td class="mono dim">{esc(snap)}</td>'
                        f'<td class="mono">{esc(inst)}</td>'
                        f'<td>{esc(before.strip())} '
                        f'<span class="chg">→</span> '
                        f'<span class="chg">{esc(after.strip())}</span></td></tr>'
                    )
                out.append('</tbody></table></div>')
            continue

        # "RI purchase opportunities" block → table
        if re.match(r'\s*RI purchase opportunities', lines[i]):
            hdr = lines[i].strip()
            i += 1
            _RI_LINE = re.compile(
                r'\s+(\S+)\s{2,}(\S+)\s{2,}(save ~\$[\d,.\-]+/mo)\s+\((.+)\)')
            ri_rows = []
            while i < len(lines) and lines[i].strip():
                m = _RI_LINE.match(lines[i])
                if m:
                    ri_rows.append((m.group(1), m.group(2), m.group(3), m.group(4)))
                i += 1
            if ri_rows:
                out.append(f'<p class="prose">{esc(hdr)}</p>')
                out.append('<div class="tbl-wrap"><table><thead><tr>'
                           '<th>Instance</th><th>Class</th>'
                           '<th class="r">Est. Saving</th><th>Note</th>'
                           '</tr></thead><tbody>')
                for inst, cls, saving, note in ri_rows:
                    out.append(f'<tr><td class="mono">{esc(inst)}</td>'
                               f'<td class="mono">{esc(cls)}</td>'
                               f'<td class="neg">{esc(saving)}</td>'
                               f'<td class="dim">{esc(note)}</td></tr>')
                out.append('</tbody></table></div>')
            continue

        # Generic label line ("Stopped instances (3) ...:" or "[ap-southeast-1]")
        # → bold prose lead-in; the rows below it dispatch to their own handler.
        if _is_label(lines[i]):
            label = lines[i].strip().rstrip(':')
            label = re.sub(r'\$\s+', '$', label)
            label = re.sub(r'\s{2,}', ' ', label)
            out.append(f'<p class="prose"><strong>{esc(label)}</strong></p>')
            i += 1
            continue

        # Key-value stat block (2+ aligned "Key : value" lines) → 2-col table
        if _kv_match(lines[i]) and i + 1 < len(lines) and _kv_match(lines[i + 1]):
            out.append('<div class="tbl-wrap"><table class="kv-table"><tbody>')
            while i < len(lines) and _kv_match(lines[i]):
                k, v = _kv_match(lines[i])
                v = re.sub(r'\$\s+', '$', v)
                ve = esc(v)
                ve = re.sub(r'(←.*)$', r'<span class="chg">\1</span>', ve)
                out.append(f'<tr><td>{esc(k)}</td><td>{ve}</td></tr>')
                i += 1
            out.append('</tbody></table></div>')
            continue

        # Aligned-columns block (2+ rows splitting on 2+ spaces) → headerless table
        if (_row_cells(lines[i]) and i + 1 < len(lines) and _row_cells(lines[i + 1])
                and not (i + 1 < len(lines) and is_sep(lines[i + 1]))):
            rows = []
            while i < len(lines) and _row_cells(lines[i]):
                if i + 1 < len(lines) and is_sep(lines[i + 1]):
                    break  # that line is a table header — leave it for the table branch
                rows.append(_row_cells(lines[i]))
                i += 1
            if len(rows) >= 2:
                ncols = max(len(r) for r in rows)
                out.append('<div class="tbl-wrap"><table><tbody>')
                for r in rows:
                    r = r + [''] * (ncols - len(r))
                    out.append('<tr>')
                    for idx, c in enumerate(r):
                        eff = idx if idx else (1 if re.match(r'^~?\$', c) else 0)
                        cls = cell_cls(c, eff)
                        attr = f' class="{cls}"' if cls else ''
                        out.append(f'<td{attr}>{esc(c)}</td>')
                    out.append('</tr>')
                out.append('</tbody></table></div>')
            else:
                for r in rows:
                    out.append(f'<p class="prose">{esc("  ".join(r))}</p>')
            continue

        # Single-token list (3+ lines, e.g. bucket names) → columned list
        if (_is_token(lines[i]) and i + 2 < len(lines)
                and _is_token(lines[i + 1]) and _is_token(lines[i + 2])):
            out.append('<ul class="token-list">')
            while i < len(lines) and _is_token(lines[i]):
                out.append(f'<li>{esc(lines[i].strip())}</li>')
                i += 1
            out.append('</ul>')
            continue

        # Pre block: collect until blank, section boundary, a table header, or the
        # start of a block that a dedicated handler above should render.
        pre_buf = []
        while i < len(lines) and lines[i].strip():
            if i + 1 < len(lines) and is_sep(lines[i + 1]):
                break
            if pre_buf and _breaks_pre(lines, i):
                break
            pre_buf.append(lines[i])
            i += 1
        if pre_buf:
            out.append(render_pre(pre_buf))

    return '\n'.join(out)

# ─── Section title formatter ──────────────────────────────────────────────────

def fmt_section_title(title):
    m = re.match(r'^([A-Za-z]?\d+[a-z]?(?:\.\s|\s·\s|\s))(.+)$', title)
    if m:
        num  = m.group(1).strip(' ·.')
        rest = m.group(2).strip()
        return f'<span class="section-num">{esc(num)}</span> {esc(rest)}'
    return esc(title)

def sec_id(title):
    """Stable anchor id for a section title: '7b · RDS ...' → 'sec-7b'."""
    m = re.match(r'^([A-Za-z]?\d+[a-z]?)\b', title.strip())
    if m:
        return 'sec-' + m.group(1).lower()
    slug = re.sub(r'[^a-z0-9]+', '-', title.strip().lower()).strip('-')
    return 'sec-' + slug[:32]

_TOC_ACRONYMS = {
    'Ecs': 'ECS', 'Rds': 'RDS', 'Ec2': 'EC2', 'Eks': 'EKS', 'Ri': 'RI',
    'Vpc': 'VPC', 'Waf': 'WAF', 'Aws': 'AWS',
    'Cloudwatch': 'CloudWatch', 'Elasticache': 'ElastiCache',
}

def toc_entry(title):
    """(number, short label) for the TOC: strip the num prefix and trailing
    parentheticals / em-dash qualifiers so labels stay scannable."""
    num, rest = '', title.strip()
    m = re.match(r'^([A-Za-z]?\d+[a-z]?)\s*·\s*(.+)$', rest)
    if m:
        num, rest = m.group(1), m.group(2)
    rest = re.split(r'\s+\(', rest)[0]
    rest = re.split(r'\s+—\s+', rest)[0]
    words = [_TOC_ACRONYMS.get(w, w) for w in rest.strip().title().split(' ')]
    return num, ' '.join(words)

# ─── Main converter ───────────────────────────────────────────────────────────

def convert(text, data_dir=None):
    lines = text.splitlines()

    # Extract header block between ═══ borders
    i = 0
    meta_lines = []
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i < len(lines) and is_border(lines[i].strip()):
        i += 1
        while i < len(lines) and not is_border(lines[i].strip()):
            meta_lines.append(lines[i])
            i += 1
        if i < len(lines): i += 1

    # Parse sections
    sections = []
    cur_title, cur_body = None, []
    while i < len(lines):
        m = SEC_RE.match(lines[i].strip())
        if m and m.group(1).strip('─ '):
            if cur_title is not None:
                sections.append((cur_title, cur_body))
            cur_title = m.group(1)
            cur_body  = []
        elif cur_title is not None:
            cur_body.append(lines[i])
        i += 1
    if cur_title is not None:
        sections.append((cur_title, cur_body))

    # Build HTML
    html = [
        '<!DOCTYPE html><html lang="en"><head>',
        '<meta charset="UTF-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        '<title>AWS Cost Report</title>',
        f'<style>{CSS}</style>',
        '</head><body><div class="page">',
    ]

    # Scroll-spy TOC (fixed sidebar on wide screens + collapsible on mobile)
    toc_links = []
    for title, _ in sections:
        num, label = toc_entry(title)
        num_html = f'<span class="toc-num">{esc(num)}</span>' if num else '<span class="toc-num"></span>'
        toc_links.append(f'<a href="#{sec_id(title)}">{num_html}{esc(label)}</a>')
    if toc_links:
        html.append('<nav class="toc"><div class="toc-hdr">Sections</div>'
                    + ''.join(toc_links) + '</nav>')

    # Header block
    html.append('<div class="rpt-header">')
    html.append('<div>')
    html.append('<h1>AWS Cost &amp; Infrastructure Analysis</h1>')
    html.append('<div class="rpt-meta">')
    for l in meta_lines:
        l = l.strip()
        if ':' in l:
            k, _, v = l.partition(':')
            html.append(f'<span class="k">{esc(k.strip())}</span><span class="v">{esc(v.strip())}</span>')
    html.append('</div></div></div>')

    if toc_links:
        html.append('<details class="toc-mobile"><summary>Sections</summary>'
                    + ''.join(toc_links) + '</details>')

    # Charts dashboard — after the Executive Summary when present, else up top
    charts_data = _load_chart_data(data_dir) if data_dir else {}
    charts_html = _render_charts(charts_data)
    has_exec = any(t.strip().upper().startswith('EXECUTIVE') for t, _ in sections)
    if charts_html and not has_exec:
        html.append(charts_html)

    # Sections
    for title, body in sections:
        is_exec = title.strip().upper().startswith('EXECUTIVE')
        is_sec1 = bool(re.match(r'^1\b', title.strip()))
        html.append(f'<h2 id="{sec_id(title)}">{fmt_section_title(title)}</h2>')
        kpi_here = is_exec or (is_sec1 and not has_exec)
        if kpi_here and charts_data:
            kpi = _render_section1_kpis(charts_data)
            if kpi:
                html.append(kpi)
        html.append(render_body(body))
        if is_exec and charts_html:
            html.append(charts_html)
        if is_sec1 and charts_data:
            insights = _render_section1_insights(charts_data)
            if insights:
                html.append(insights)

    # Scroll-spy: highlight the TOC link for the section currently in view
    html.append("""<script>
(function () {
  var heads = Array.prototype.slice.call(document.querySelectorAll('h2[id]'));
  var links = Array.prototype.slice.call(document.querySelectorAll('.toc a'));
  if (!heads.length || !links.length) return;
  var byHref = {};
  links.forEach(function (l) { byHref[l.getAttribute('href')] = l; });
  var active = null, ticking = false;
  function spy() {
    ticking = false;
    var y = window.scrollY + 120, cur = heads[0];
    for (var i = 0; i < heads.length; i++) {
      if (heads[i].offsetTop <= y) cur = heads[i]; else break;
    }
    var link = byHref['#' + cur.id];
    if (link === active) return;
    if (active) active.classList.remove('active');
    active = link;
    if (active) {
      active.classList.add('active');
      if (active.scrollIntoView) active.scrollIntoView({ block: 'nearest' });
    }
  }
  window.addEventListener('scroll', function () {
    if (!ticking) { ticking = true; requestAnimationFrame(spy); }
  }, { passive: true });
  // collapse the mobile TOC after tapping a link
  var mob = document.querySelector('.toc-mobile');
  if (mob) mob.addEventListener('click', function (e) {
    if (e.target.tagName === 'A') mob.removeAttribute('open');
  });
  spy();
})();
</script>""")
    html.append('</div></body></html>')
    return '\n'.join(html)

if __name__ == '__main__':
    args = sys.argv[1:]
    data_dir = None
    if '--data' in args:
        idx = args.index('--data')
        if idx + 1 < len(args):
            data_dir = args[idx + 1]
    print(convert(sys.stdin.read(), data_dir=data_dir))
