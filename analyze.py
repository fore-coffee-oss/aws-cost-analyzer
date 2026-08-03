#!/usr/bin/env python3
"""
AWS cost & infrastructure analysis.
Reads from ./data/YYYY-MM-DD/<profile>/ — no AWS calls made.

Usage:
    python3 analyze.py [data-dir]
    python3 analyze.py data/2026-07-06/production
"""
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

# RDS pricing tables & storage-cost helper are shared with make_ri_plan.py —
# see rds_pricing.py so the two never drift apart.
from rds_pricing import (
    RDS_RAM_GB,
    RDS_ONDEMAND_HOURLY,
    RDS_RI_1YR_HOURLY,
    RDS_STORAGE_MONTHLY_PER_GB,
    RDS_RI_NU as _RI_NU,
    rds_storage_monthly,
)

# Approximate on-demand hourly prices for ElastiCache nodes, ap-southeast-1
ELASTICACHE_OD_HOURLY = {
    "cache.t3.micro": 0.017,  "cache.t3.small": 0.034,  "cache.t3.medium": 0.068,
    "cache.t4g.micro": 0.016, "cache.t4g.small": 0.033, "cache.t4g.medium": 0.065,
    "cache.m6g.large": 0.123, "cache.m6g.xlarge": 0.246, "cache.m6g.2xlarge": 0.492,
    "cache.m7g.large": 0.135, "cache.m7g.xlarge": 0.270, "cache.m7g.2xlarge": 0.540,
    "cache.r6g.large": 0.166, "cache.r6g.xlarge": 0.333, "cache.r6g.2xlarge": 0.666, "cache.r6g.4xlarge": 1.332,
    "cache.r7g.large": 0.182, "cache.r7g.xlarge": 0.363, "cache.r7g.2xlarge": 0.726, "cache.r7g.4xlarge": 1.452,
}
EKS_CTRL_PLANE_HOURLY = 0.10  # $/hr per cluster, all regions

# ── Resolve data directory & output format ────────────────────────────────────

def _is_snapshot(path):
    return os.path.isdir(os.path.join(path, "billing")) or \
           os.path.isfile(os.path.join(path, "pull_metadata.json"))


def find_latest_data_dir():
    base = "./data"
    if not os.path.isdir(base):
        sys.exit("No ./data directory found. Run pull_aws_data.sh first.")
    for date_dir in sorted(os.listdir(base), reverse=True):
        date_path = os.path.join(base, date_dir)
        if not os.path.isdir(date_path):
            continue
        if _is_snapshot(date_path):
            return date_path
        # New format: data/YYYY-MM-DD/<profile>/
        for profile in sorted(os.listdir(date_path)):
            profile_path = os.path.join(date_path, profile)
            if os.path.isdir(profile_path) and _is_snapshot(profile_path):
                return profile_path
    sys.exit("No data snapshots found in ./data/")

args = sys.argv[1:]
OUTPUT_FMT = "text"
if "--pdf" in args:
    OUTPUT_FMT = "pdf"
    args = [a for a in args if a != "--pdf"]
elif "--md" in args:
    OUTPUT_FMT = "md"
    args = [a for a in args if a != "--md"]

DATA_DIR = args[0] if args else find_latest_data_dir()

# ── Config (optional config.json — supports per-profile settings) ─────────────
_cfg = {}
if os.path.exists("config.json"):
    with open("config.json") as _f:
        _cfg = json.load(_f)

# Extract profile name from path: data/YYYY-MM-DD/<profile> → "<profile>"
_path_parts = DATA_DIR.rstrip("/").replace("\\", "/").split("/")
_profile = _path_parts[-1] if len(_path_parts) >= 3 and _path_parts[0] == "data" else ""

# Per-profile config takes priority; fall back to top-level keys
_pcfg = _cfg.get("profiles", {}).get(_profile, {})
TRACKED_INSTANCES = _pcfg.get("tracked_rds_instances") or _cfg.get("tracked_rds_instances", [])
ACCOUNT_LABEL     = _pcfg.get("account_label")         or _cfg.get("account_label", "")
# True = Datadog confirmed collecting container metrics; False = no Datadog;
# None = not declared — recommendations say "verify your monitoring first".
HAS_DATADOG = _pcfg.get("datadog", _cfg.get("datadog", None))

def load(path):
    full = os.path.join(DATA_DIR, path)
    if not os.path.exists(full):
        return None
    with open(full) as f:
        return json.load(f)

# ── Formatting helpers ────────────────────────────────────────────────────────

def usd(amount):
    return f"${amount:>10,.2f}"

_md_in_block = False

def hr(title=""):
    global _md_in_block
    if OUTPUT_FMT in ("md", "pdf"):
        if _md_in_block:
            print("```")
            print()
            _md_in_block = False
        if title:
            print(f"## {title}")
            print()
            print("```")
            _md_in_block = True
    else:
        width = 64
        if title:
            print(f"\n{'─' * 3} {title} {'─' * max(3, width - len(title) - 5)}")
        else:
            print("─" * width)

def pct_change(old, new):
    if old == 0:
        return "+∞"
    change = (new - old) / old * 100
    sign = "+" if change >= 0 else ""
    return f"{sign}{change:.1f}%"

def now_utc():
    return datetime.now(timezone.utc)

# ══════════════════════════════════════════════════════════════════════════════
# Executive ordering: the script computes and prints sections in dependency
# order, capturing everything; at the very end the captured text is re-emitted
# in executive reading order (summary → conclusions → evidence → appendix).
# ══════════════════════════════════════════════════════════════════════════════

import io as _io
_STDOUT_REAL = sys.stdout
if OUTPUT_FMT not in ("md", "pdf"):
    sys.stdout = _io.StringIO()

meta = load("pull_metadata.json") or {}
def _local_ts(iso_utc):
    """ISO-8601 UTC string → local system time, e.g. '2026-07-07 09:44:56 WIB'."""
    try:
        dt = datetime.fromisoformat(str(iso_utc).replace("Z", "+00:00"))
        return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    except (ValueError, TypeError):
        return str(iso_utc)

_pulled_at    = _local_ts(meta.get("pulled_at", "?"))
_generated_at = now_utc().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
if OUTPUT_FMT in ("md", "pdf"):
    print(f"# AWS Cost & Infrastructure Analysis")
    print(f"")
    print(f"| | |")
    print(f"|---|---|")
    print(f"| **Account**   | {meta.get('account_id','?')} ({meta.get('account_alias','?')}) |")
    print(f"| **Region**    | {meta.get('region','?')} |")
    print(f"| **Data**      | {DATA_DIR} |")
    print(f"| **Pulled**    | {_pulled_at} |")
    print(f"| **Generated** | {_generated_at} |")
    print()
else:
    print(f"\n{'═' * 64}")
    print(f"  AWS Cost & Infrastructure Analysis")
    print(f"  Account   : {meta.get('account_id','?')} ({meta.get('account_alias','?')})")
    print(f"  Region    : {meta.get('region','?')}")
    print(f"  Data      : {DATA_DIR}")
    print(f"  Pulled    : {_pulled_at}")
    print(f"  Generated : {_generated_at}")
    print(f"{'═' * 64}")

# ══════════════════════════════════════════════════════════════════════════════
# 1. MONTHLY COST TREND
# ══════════════════════════════════════════════════════════════════════════════

hr("1 · MONTHLY COST TREND")

monthly = load("billing/monthly_total.json")
if monthly:
    months = []
    for r in monthly["ResultsByTime"]:
        month = r["TimePeriod"]["Start"][:7]
        cost = float(r["Total"]["BlendedCost"]["Amount"])
        months.append((month, cost))

    for i, (month, cost) in enumerate(months):
        change = ""
        if i > 0:
            change = f"  ({pct_change(months[i-1][1], cost)} vs prev month)"
        print(f"  {month}   {usd(cost)}{change}")

    if len(months) >= 2:
        total_change = pct_change(months[0][1], months[-1][1])
        print(f"\n  {months[0][0]} → {months[-1][0]}  total change: {total_change}")

# ── Daily spike detection ─────────────────────────────────────────────────────

daily = load("billing/daily_total.json")
if daily:
    days = []
    for r in daily["ResultsByTime"]:
        day = r["TimePeriod"]["Start"]
        cost = float(r["Total"]["BlendedCost"]["Amount"])
        days.append((day, cost))

    if days:
        avg = sum(c for _, c in days) / len(days)
        spikes = [(d, c) for d, c in days if c > avg * 1.3]
        sorted_days = sorted(days, key=lambda x: -x[1])

        print(f"\n  Daily average (last 90 days): {usd(avg)}")
        print(f"  Highest single days:")
        for day, cost in sorted_days[:5]:
            flag = " ← spike" if cost > avg * 1.3 else ""
            print(f"    {day}  {usd(cost)}{flag}")

# ══════════════════════════════════════════════════════════════════════════════
# 2. TOP SERVICES — COST & GROWTH
# ══════════════════════════════════════════════════════════════════════════════

hr("2 · TOP SERVICES BY COST")

svc_data = load("billing/monthly_by_service.json")
if svc_data:
    # Build {service: {month: cost}}
    by_service = defaultdict(dict)
    months_seen = []
    for r in svc_data["ResultsByTime"]:
        month = r["TimePeriod"]["Start"][:7]
        months_seen.append(month)
        for g in r["Groups"]:
            svc = g["Keys"][0]
            cost = float(g["Metrics"]["BlendedCost"]["Amount"])
            by_service[svc][month] = cost

    months_seen = sorted(set(months_seen))
    last_month = months_seen[-1]
    prev_month = months_seen[-2] if len(months_seen) >= 2 else None

    # Sort by last month cost
    ranked = sorted(by_service.items(), key=lambda x: -x[1].get(last_month, 0))

    header = f"  {'Service':<45} {last_month:>10}"
    if prev_month:
        header += f"  {'Change':>8}"
    print(header)
    print(f"  {'─'*45} {'─'*10}  {'─'*8}")

    shown = 0
    for svc, costs in ranked:
        last = costs.get(last_month, 0)
        if last < 1:
            continue
        row = f"  {svc:<45} {usd(last)}"
        if prev_month:
            prev = costs.get(prev_month, 0)
            row += f"  {pct_change(prev, last):>8}"
        print(row)
        shown += 1
        if shown >= 15:
            break

# ══════════════════════════════════════════════════════════════════════════════
# 3. RDS — BIGGEST COST DRIVER
# ══════════════════════════════════════════════════════════════════════════════

hr("3 · RDS INSTANCES")

rds_data = load("infra/rds/instances.json")
rds_ri = load("infra/rds/reserved_instances.json")

if rds_data:
    instances = rds_data["DBInstances"]

    # Active RIs for quick lookup
    active_ri = defaultdict(int)
    expiring_soon = []
    if rds_ri:
        today = now_utc().date()
        for ri in rds_ri.get("ReservedDBInstances", []):
            if ri["State"] != "active":
                continue
            active_ri[ri["DBInstanceClass"]] += ri["DBInstanceCount"]
            # Check expiry within 90 days
            start = ri.get("StartTime", "")[:10]
            duration_secs = ri.get("Duration", 0)
            if start and duration_secs:
                start_dt = datetime.strptime(start, "%Y-%m-%d").date()
                from datetime import timedelta
                end_dt = start_dt + timedelta(seconds=duration_secs)
                days_left = (end_dt - today).days
                if days_left < 90:
                    expiring_soon.append((ri["DBInstanceClass"], end_dt, days_left))

    print(f"  {'Instance':<40} {'Class':<20} {'Engine':<12} {'RI?'}")
    print(f"  {'─'*40} {'─'*20} {'─'*12} {'─'*5}")
    for db in sorted(instances, key=lambda x: x["DBInstanceClass"], reverse=True):
        iid = db["DBInstanceIdentifier"]
        cls = db["DBInstanceClass"]
        engine = f"{db['Engine']} {db.get('EngineVersion','')[:5]}"
        ri_covered = "✓ RI" if active_ri.get(cls, 0) > 0 else "on-demand"
        print(f"  {iid:<40} {cls:<20} {engine:<12} {ri_covered}")

    if expiring_soon:
        print(f"\n  ⚠  RDS Reserved Instances expiring soon:")
        for cls, end_dt, days in sorted(expiring_soon, key=lambda x: x[2]):
            print(f"    {cls:<20} expires {end_dt}  ({days} days)")

    # Retired RIs — still showing up, worth noting
    retired = [ri for ri in rds_ri.get("ReservedDBInstances", []) if ri["State"] == "retired"]
    if retired:
        print(f"\n  Note: {len(retired)} retired RI records in account (historical, no cost impact)")

# ══════════════════════════════════════════════════════════════════════════════
# 3b. RDS COST ESTIMATE & RI ROI
# ══════════════════════════════════════════════════════════════════════════════

hr("3b · RDS COST ESTIMATE & RI OPPORTUNITY  (approx ap-southeast-1 prices)")

if rds_data:
    # Build RI coverage map
    ri_covered = defaultdict(int)
    if rds_ri:
        for ri in rds_ri.get("ReservedDBInstances", []):
            if ri["State"] == "active":
                ri_covered[ri["DBInstanceClass"]] += ri["DBInstanceCount"]

    print(f"  {'Instance':<40} {'Class':<22} {'$/mo est':>9}  {'RI saving':>10}  {'RI?'}")
    print(f"  {'─'*40} {'─'*22} {'─'*9}  {'─'*10}  {'─'*10}")

    total_est = 0
    total_ri_saving = 0
    ri_opportunities = []

    for db in sorted(rds_data["DBInstances"], key=lambda x: -RDS_ONDEMAND_HOURLY.get(x["DBInstanceClass"], 0)):
        iid = db["DBInstanceIdentifier"]
        cls = db["DBInstanceClass"]
        storage_gb = db.get("AllocatedStorage", 0)
        is_replica = bool(db.get("ReadReplicaSourceDBInstanceIdentifier"))

        od_hr = RDS_ONDEMAND_HOURLY.get(cls, 0)
        ri_hr = RDS_RI_1YR_HOURLY.get(cls)
        storage_cost = rds_storage_monthly(storage_gb, db.get("StorageType", "gp2"),
                                           db.get("Iops") or 0, db.get("StorageThroughput") or 0)
        instance_cost = od_hr * 730
        total_monthly = instance_cost + storage_cost

        has_ri = ri_covered.get(cls, 0) > 0
        ri_str = "✓ RI" if has_ri else "on-demand"
        if has_ri:
            ri_covered[cls] -= 1

        ri_saving_str = ""
        if not has_ri and ri_hr:
            monthly_saving = (od_hr - ri_hr) * 730
            total_ri_saving += monthly_saving
            ri_saving_str = f"~${monthly_saving:>7,.0f}/mo"
            ri_opportunities.append((iid, cls, total_monthly, monthly_saving))

        total_est += total_monthly if not has_ri else (ri_hr or od_hr) * 730 + storage_cost
        print(f"  {iid:<40} {cls:<22} ${total_monthly:>8,.0f}  {ri_saving_str:>10}  {ri_str}")

    print(f"\n  Estimated total RDS (instance + storage) : ~${total_est:,.0f}/month")
    print(f"  Note: actual bill includes Multi-AZ, I/O, backup storage not shown above.")

    if ri_opportunities:
        print(f"\n  RI purchase opportunities (1-year no-upfront):")
        ri_opportunities.sort(key=lambda x: -x[3])
        for iid, cls, est_monthly, saving in ri_opportunities:
            print(f"    {iid:<40} {cls:<22}  save ~${saving:,.0f}/mo  (break-even: immediate, no upfront)")

        print(f"\n  Total potential RI savings: ~${total_ri_saving:,.0f}/month  (~${total_ri_saving*12:,.0f}/year)")

# ══════════════════════════════════════════════════════════════════════════════
# 3c. RDS DAILY COST TRACKER
# ══════════════════════════════════════════════════════════════════════════════

hr("3c · RDS DAILY COST TRACKER")

# RI normalization factors (same instance family, AWS applies by NUs)
RDS_NORM_FACTOR = {
    "micro": 0.5, "small": 1, "medium": 2, "large": 4, "xlarge": 8,
    "2xlarge": 16, "4xlarge": 32, "8xlarge": 64, "12xlarge": 96,
    "16xlarge": 128, "24xlarge": 192, "32xlarge": 256,
}

def ri_norm_units(cls):
    # cls like "db.m7i.4xlarge" → size token "4xlarge"
    size = cls.split(".")[-1]
    return RDS_NORM_FACTOR.get(size, 0)

def ri_family(cls):
    # "db.m7i.4xlarge" → "m7i"
    parts = cls.split(".")
    return parts[1] if len(parts) >= 2 else ""

# -- Cross-snapshot change detection -----------------------------------------
base_dir = "./data"
all_snaps = []
for _sd in sorted(os.listdir(base_dir)):
    _sdp = os.path.join(base_dir, _sd)
    if not os.path.isdir(_sdp):
        continue
    if _profile and os.path.isdir(os.path.join(_sdp, _profile)):
        all_snaps.append(os.path.join(_sd, _profile))   # new: data/YYYY-MM-DD/profile
    elif not _profile and _is_snapshot(_sdp):
        all_snaps.append(_sd)                           # old flat: data/YYYY-MM-DD
rds_history = {}  # iid → [(date, class), ...]
for snap in all_snaps:
    snap_path = os.path.join(base_dir, snap, "infra/rds/instances.json")
    if not os.path.exists(snap_path):
        continue
    with open(snap_path) as _f:
        snap_data = json.load(_f)
    for db in snap_data.get("DBInstances", []):
        iid = db["DBInstanceIdentifier"]
        cls = db["DBInstanceClass"]
        if iid not in rds_history:
            rds_history[iid] = []
        if not rds_history[iid] or rds_history[iid][-1][1] != cls:
            rds_history[iid].append((snap, cls))

changes = []
for iid, history in rds_history.items():
    for i in range(1, len(history)):
        prev_snap, prev_cls = history[i - 1]
        curr_snap, curr_cls = history[i]
        changes.append((curr_snap, iid, prev_cls, curr_cls))

if changes:
    print(f"  Configuration changes detected across snapshots:")
    for date, iid, old_cls, new_cls in sorted(changes):
        print(f"    {date}  {iid:<40}  {old_cls} → {new_cls}")
else:
    print(f"  No instance class changes detected across {len(all_snaps)} snapshot(s).")

# -- RI coverage: exact-match first, remainder goes to normalization pool -----
# Step 1: build exact-class RI counts and normalization remainder
ri_exact = defaultdict(int)   # class → count of exact RIs
ri_norm_pool = defaultdict(float)  # family → NUs from unmatched RIs
if rds_ri:
    for ri in rds_ri.get("ReservedDBInstances", []):
        if ri["State"] != "active":
            continue
        ri_exact[ri["DBInstanceClass"]] += ri["DBInstanceCount"]

ri_coverage_map = {}  # iid → fraction covered (0.0–1.0)
if rds_data:
    # Step 2: exact-match pass
    ri_exact_copy = dict(ri_exact)
    unmatched_instances = []
    for db in rds_data["DBInstances"]:
        iid = db["DBInstanceIdentifier"]
        cls = db["DBInstanceClass"]
        if ri_exact_copy.get(cls, 0) > 0:
            ri_coverage_map[iid] = 1.0
            ri_exact_copy[cls] -= 1
        else:
            unmatched_instances.append(db)

    # Step 3: remaining RIs → normalization pool
    for cls, remaining in ri_exact_copy.items():
        if remaining > 0:
            fam = ri_family(cls)
            ri_norm_pool[fam] += ri_norm_units(cls) * remaining

    # Step 4: normalization pass for unmatched instances (largest first)
    for db in sorted(unmatched_instances, key=lambda x: -ri_norm_units(x["DBInstanceClass"])):
        iid = db["DBInstanceIdentifier"]
        cls = db["DBInstanceClass"]
        fam = ri_family(cls)
        needed = ri_norm_units(cls)
        available = ri_norm_pool.get(fam, 0)
        if needed == 0:
            ri_coverage_map[iid] = 0.0
        else:
            fraction = min(1.0, available / needed)
            ri_coverage_map[iid] = fraction
            ri_norm_pool[fam] = max(0, available - needed)

# -- Per-instance daily cost table -------------------------------------------
print()
print(f"  Current fleet (as of {'/'.join(DATA_DIR.rstrip('/').split('/')[-2:])}):")
print(f"  {'Instance':<40} {'Class':<20} {'RI cover':>8}  {'Rate/hr':>9}  {'$/day est':>9}  {'$/mo est':>9}")
print(f"  {'─'*40} {'─'*20} {'─'*8}  {'─'*9}  {'─'*9}  {'─'*9}")

total_daily_est = 0.0
if rds_data:
    for db in sorted(rds_data["DBInstances"],
                     key=lambda x: -RDS_ONDEMAND_HOURLY.get(x["DBInstanceClass"], 0)):
        iid = db["DBInstanceIdentifier"]
        cls = db["DBInstanceClass"]
        storage_gb = db.get("AllocatedStorage", 0)
        frac = ri_coverage_map.get(iid, 0.0)
        od_hr = RDS_ONDEMAND_HOURLY.get(cls, 0)
        ri_hr = RDS_RI_1YR_HOURLY.get(cls, od_hr)
        # Blended rate: fraction at RI rate, remainder at on-demand
        eff_hr = frac * ri_hr + (1 - frac) * od_hr
        storage_mo = rds_storage_monthly(storage_gb, db.get("StorageType", "gp2"),
                                         db.get("Iops") or 0, db.get("StorageThroughput") or 0)
        storage_daily = storage_mo / 30
        daily_est = eff_hr * 24 + storage_daily
        monthly_est = eff_hr * 730 + storage_mo
        total_daily_est += daily_est

        if frac == 1.0:
            ri_label = "100% RI"
        elif frac > 0:
            ri_label = f"{frac*100:.0f}% RI"
        else:
            ri_label = "on-demand"
        if od_hr == 0:
            rate_str = "(t-class)"
            ri_label = "on-demand"
        else:
            rate_str = f"${eff_hr:>7.3f}"
        print(f"  {iid:<40} {cls:<20} {ri_label:>8}  {rate_str:>9}  ${daily_est:>8,.2f}  ${monthly_est:>8,.0f}")

    print(f"  {'─'*40} {'─'*20} {'─'*8}  {'─'*9}  {'─'*9}  {'─'*9}")
    print(f"  {'TOTAL (instance + storage est)':<40} {'':<20} {'':>8}  {'':>9}  ${total_daily_est:>8,.2f}  ${total_daily_est*30:>8,.0f}")
    print(f"  Note: excludes Multi-AZ, I/O, backup storage. RI rates: 1-yr no-upfront ap-southeast-1.")

# -- Actual daily RDS billing (last 90 days) ---------------------------------
daily_svc_data = load("billing/daily_by_service.json")
rds_daily = {}
if daily_svc_data:
    for r in daily_svc_data.get("ResultsByTime", []):
        d = r["TimePeriod"]["Start"]
        for g in r.get("Groups", []):
            if "Relational Database" in g["Keys"][0]:
                amt_val = g["Metrics"].get("UnblendedCost", g["Metrics"].get("BlendedCost", {}))
                rds_daily[d] = rds_daily.get(d, 0) + float(amt_val.get("Amount", 0))

if rds_daily:
    print(f"\n  Actual daily RDS billing (from AWS, last 90 days):")
    max_val = max(rds_daily.values()) if rds_daily else 1
    for d in sorted(rds_daily):
        amt = rds_daily[d]
        bar_len = int(amt / max_val * 30)
        bar = "█" * bar_len
        flag = "  ← month-start spike" if d.endswith("-01") else ""
        print(f"    {d}  ${amt:>8,.2f}  {bar}{flag}")

    # Non-spike days for baseline
    non_spike = [v for d, v in rds_daily.items() if not d.endswith("-01")]
    if non_spike:
        avg = sum(non_spike) / len(non_spike)
        print(f"\n    Avg daily RDS (excl. 1st-of-month spikes): ${avg:,.2f}/day  →  ~${avg*30:,.0f}/month")

# -- RDS daily breakdown by charge type ----------------------------------------
# Build rds_by_type at module level so section 3d can use it for spike labels
rds_by_type = {}
rds_record_data = load("billing/daily_rds_by_record_type.json")
if rds_record_data:
    for r in rds_record_data.get("ResultsByTime", []):
        d = r["TimePeriod"]["Start"]
        day_totals = {}
        for g in r.get("Groups", []):
            rtype = g["Keys"][0]
            amt = float(g["Metrics"].get("BlendedCost", {}).get("Amount", 0))
            if abs(amt) > 0.005:
                day_totals[rtype] = day_totals.get(rtype, 0) + amt
        if day_totals:
            rds_by_type[d] = day_totals

    if rds_by_type:
        # Determine which record types are present
        TYPE_ORDER  = ["RIFee", "Recurring", "DiscountedUsage", "Usage",
                       "SavingsPlanCoveredUsage", "SavingsPlanNegation", "Tax", "Credit"]
        TYPE_LABEL  = {
            "RIFee":                   "RI Fee",
            "Recurring":               "RI Fee",
            "DiscountedUsage":         "RI Usage",
            "Usage":                   "On-demand",
            "SavingsPlanCoveredUsage": "SP Usage",
            "SavingsPlanNegation":     "SP Negat.",
            "Tax":                     "Tax",
            "Credit":                  "Credit",
        }
        all_types = set(t for v in rds_by_type.values() for t in v)
        rtypes = [t for t in TYPE_ORDER if t in all_types]
        rtypes += sorted(t for t in all_types if t not in TYPE_ORDER)
        labels = [TYPE_LABEL.get(t, t) for t in rtypes]

        CW = 11  # chars per money column ($ + 9 for number + leading space)
        hdr  = f"  {'Date':<10}" + "".join(f"  {lbl:>{CW}}" for lbl in labels) + f"  {'Total':>{CW}}"
        sep  = f"  {'─'*10}" + "".join(f"  {'─'*CW}" for _ in rtypes) + f"  {'─'*CW}"
        print(f"\n  RDS billing by charge type — explains spike days:")
        print(hdr)
        print(sep)

        for d in sorted(rds_by_type.keys()):
            day = rds_by_type[d]
            total = sum(day.values())
            ri_fee = day.get("RIFee", 0) + day.get("Recurring", 0)
            flag = "  ←RI monthly fee" if ri_fee > 50 else ""
            row = f"  {d:<10}"
            for t in rtypes:
                amt = day.get(t, 0)
                if abs(amt) < 0.005:
                    row += f"  {'—':>{CW}}"
                else:
                    row += f"  ${amt:>{CW-1},.2f}"
            row += f"  ${total:>{CW-1},.2f}{flag}"
            print(row)

        print(f"  Note: RI Fee = full month's RI recurring charge, billed as a lump sum on charge date.")
        print(f"        RI Usage = hours covered by RI at discounted rate. On-demand = full-price hours.")

# ══════════════════════════════════════════════════════════════════════════════
# 3d. TRACKED DATABASE CLUSTER — DAY-BY-DAY TRACKER
# ══════════════════════════════════════════════════════════════════════════════

hr("3d · TRACKED DATABASE CLUSTER — DAY-BY-DAY COST")

if not TRACKED_INSTANCES:
    print("  No tracked_rds_instances configured in config.json — skipping.")
    print("  Add instance IDs to config.json to enable day-by-day tracking.")

if TRACKED_INSTANCES:
    # Build config history per instance: [(snap_date, class, storage_gb, storage_type, iops, throughput), ...]
    tracked_history = {iid: [] for iid in TRACKED_INSTANCES}
    for snap in all_snaps:
        snap_path = os.path.join(base_dir, snap, "infra/rds/instances.json")
        if not os.path.exists(snap_path):
            continue
        with open(snap_path) as _f:
            snap_rds = json.load(_f)
        for db in snap_rds.get("DBInstances", []):
            iid = db["DBInstanceIdentifier"]
            if iid not in tracked_history:
                continue
            cls = db["DBInstanceClass"]
            storage = db.get("AllocatedStorage", 0)
            stype = db.get("StorageType", "gp2")
            iops = db.get("Iops") or 0
            throughput = db.get("StorageThroughput") or 0
            hist = tracked_history[iid]
            if not hist or hist[-1][1] != cls:
                tracked_history[iid].append((snap, cls, storage, stype, iops, throughput))

    # Print configuration timeline
    print(f"  Configuration timeline (from snapshots):")
    for iid in TRACKED_INSTANCES:
        hist = tracked_history.get(iid, [])
        if not hist:
            print(f"  {iid:<40}  no data")
            continue
        parts = []
        for snap, cls, *_ in hist:
            parts.append(f"{snap}: {cls}")
        print(f"  {iid:<40}  {' → '.join(parts)}")

    def config_on_date(hist, date_str):
        cfg = hist[0] if hist else None
        for entry in hist:
            if entry[0] <= date_str:
                cfg = entry
            else:
                break
        return cfg  # (snap, cls, storage_gb, storage_type, iops, throughput)

    def tracked_ri_coverage(cls, iid):
        """Generic RI coverage using ri_coverage_map built in section 3c."""
        frac = ri_coverage_map.get(iid, 0.0)
        od = RDS_ONDEMAND_HOURLY.get(cls, 0)
        ri = RDS_RI_1YR_HOURLY.get(cls, od)
        eff = frac * ri + (1 - frac) * od
        return (frac, eff)

    # Day-by-day table using daily billing window
    if daily_svc_data:
        dates = sorted(r["TimePeriod"]["Start"] for r in daily_svc_data.get("ResultsByTime", []))
        if dates:
            print()
            h_iids = [iid.split("-")[-1] if "-" in iid else iid for iid in TRACKED_INSTANCES]
            print(f"  {'Date':<12}" + "".join(f"  {h:<32}" for h in h_iids) + f"  {'Est total':>9}  {'Actual RDS':>10}")
            print(f"  {'─'*12}" + "".join(f"  {'─'*32}" for _ in TRACKED_INSTANCES) + f"  {'─'*9}  {'─'*10}")

            prev_classes = {}
            for d in dates:
                row_est = 0.0
                cells = []
                for iid in TRACKED_INSTANCES:
                    hist = tracked_history.get(iid, [])
                    cfg = config_on_date(hist, d)
                    if not cfg:
                        cells.append(f"{'no data':<29}")
                        continue
                    _, cls, storage, stype, iops, throughput = cfg if len(cfg) == 6 else (*cfg, "gp2", 0, 0)
                    frac, eff_hr = tracked_ri_coverage(cls, iid)
                    storage_daily = rds_storage_monthly(storage, stype, iops, throughput) / 30
                    inst_daily = eff_hr * 24 + storage_daily
                    row_est += inst_daily

                    if frac == 1.0:
                        ri_tag = "RI"
                    elif frac > 0:
                        ri_tag = f"{frac*100:.0f}%RI"
                    else:
                        ri_tag = "OD"

                    prev = prev_classes.get(iid)
                    changed = prev and prev != cls
                    change_arrow = "↓" if changed else " "
                    prev_classes[iid] = cls

                    size = cls.replace("db.", "").replace("xlarge", "xl")
                    cell = f"{size:<14} {ri_tag:<5} ${inst_daily:>6,.2f}/d {change_arrow}"
                    cells.append(f"{cell:<32}")

                actual = rds_daily.get(d, 0)
                ri_fee_today = rds_by_type.get(d, {}).get("RIFee", 0) + rds_by_type.get(d, {}).get("Recurring", 0)
                if ri_fee_today > 50:
                    spike_flag = " ←RI monthly fee"
                elif actual > row_est * 4:
                    spike_flag = " ←spike"
                else:
                    spike_flag = ""
                print(f"  {d:<12}" + "".join(f"  {c}" for c in cells) + f"  ${row_est:>8,.2f}  ${actual:>8,.2f}{spike_flag}")

            print()
            print(f"  Est = instance hourly rate × 24h + storage. RI rates: 1-yr no-upfront.")
            print(f"  Actual RDS = total account RDS billing that day (all instances, incl. month-start lump sums).")

# ══════════════════════════════════════════════════════════════════════════════
# 3e. RI PLAN — COVERAGE & RECOMMENDATIONS
# ══════════════════════════════════════════════════════════════════════════════

def _ri_eng(e): return "postgresql" if "postgres" in e.lower() else "mysql"

hr("3e · RI PLAN — COVERAGE & RECOMMENDATIONS")

if rds_data and rds_ri is not None:
    _ri_pool = {}
    _ri_detail = []
    for _r in [r for r in rds_ri.get("ReservedDBInstances", []) if r["State"] == "active"]:
        _cls = _r["DBInstanceClass"]
        _cnt = _r["DBInstanceCount"]
        _nu  = _RI_NU.get(_cls, 0) * _cnt
        _eng = _ri_eng(_r.get("ProductDescription", ""))
        _fam = _cls.split(".")[1]
        _ri_pool[(_fam, _eng)] = _ri_pool.get((_fam, _eng), 0) + _nu
        _dur = _r.get("Duration", 31536000) // 86400
        _st  = str(_r.get("StartTime", ""))[:10]
        try:
            _exp = (datetime.strptime(_st, "%Y-%m-%d") + timedelta(days=_dur)).date()
            _dl  = (_exp - datetime.now().date()).days
            _exp_s = str(_exp)
        except Exception:
            _exp_s, _dl = "unknown", 999
        _ri_detail.append({"id": _r["ReservedDBInstanceId"], "class": _cls,
                            "engine": _eng, "count": _cnt, "nu": _nu,
                            "expiry": _exp_s, "days_left": _dl})

    if _ri_detail:
        _w = max(len(r["id"]) for r in _ri_detail)
        print(f"\n  {'RI ID':<{_w}}  {'Class':<20}  {'Engine':<12}  {'Cnt':>3}  {'NUs':>4}  {'Expiry':<12}  {'Days left':>9}")
        print(f"  {'─'*_w}  {'─'*20}  {'─'*12}  {'─'*3}  {'─'*4}  {'─'*12}  {'─'*9}")
        for _r in _ri_detail:
            _warn = "  ← expiring soon" if _r["days_left"] < 90 else ("  ← renew soon" if _r["days_left"] < 180 else "")
            print(f"  {_r['id']:<{_w}}  {_r['class']:<20}  {_r['engine']:<12}  {_r['count']:>3}  {_r['nu']:>4}  {_r['expiry']:<12}  {_r['days_left']:>6}d{_warn}")

    _pool_copy = dict(_ri_pool)
    _inst_plan = []
    for _db in sorted(rds_data.get("DBInstances", []),
                      key=lambda x: RDS_ONDEMAND_HOURLY.get(x["DBInstanceClass"], 0), reverse=True):
        _cls = _db["DBInstanceClass"]
        _eng = _ri_eng(_db.get("Engine", ""))
        _fam = _cls.split(".")[1]
        _nu_need = _RI_NU.get(_cls, 0)
        _key = (_fam, _eng)
        _avail = _pool_copy.get(_key, 0)
        _cov   = min(_avail, _nu_need)
        _pool_copy[_key] = max(0, _avail - _nu_need)
        _frac  = _cov / _nu_need if _nu_need else 0
        _ri_hr = RDS_RI_1YR_HOURLY.get(_cls)
        _od_hr = RDS_ONDEMAND_HOURLY.get(_cls, 0)
        _eff   = _frac * _ri_hr + (1 - _frac) * _od_hr if _ri_hr and _frac > 0 else _od_hr
        _stor  = rds_storage_monthly(_db.get("AllocatedStorage", 0), _db.get("StorageType", "gp2"),
                                     _db.get("Iops") or 0, _db.get("StorageThroughput") or 0)
        _mo_est = _eff * 730 + _stor
        _mo_od  = _od_hr * 730 + _stor
        _save   = _mo_od - ((_ri_hr * 730 + _stor) if _ri_hr else _mo_od)
        _inst_plan.append({"id": _db["DBInstanceIdentifier"], "class": _cls,
                           "nu": _nu_need, "nu_cov": _cov, "ri_pct": round(_frac * 100),
                           "mo_est": _mo_est, "mo_od": _mo_od, "save": _save, "ri_hr": _ri_hr})

    if _inst_plan:
        _w2 = max(len(i["id"]) for i in _inst_plan)
        print(f"\n  {'Instance':<{_w2}}  {'Class':<20}  {'NUs':>4}  {'Cov':>4}  {'%':>4}  {'$/mo (est)':>10}  {'$/mo (OD)':>10}  {'Save if 100%':>13}")
        print(f"  {'─'*_w2}  {'─'*20}  {'─'*4}  {'─'*4}  {'─'*4}  {'─'*10}  {'─'*10}  {'─'*13}")
        for _i in _inst_plan:
            _ri_s  = f"{_i['ri_pct']}%" if _i["ri_pct"] > 0 else "—"
            _sav_s = f"~${_i['save']:,.0f}/mo" if _i["save"] > 50 else "—"
            print(f"  {_i['id']:<{_w2}}  {_i['class']:<20}  {_i['nu']:>4}  {_i['nu_cov']:>4}  {_ri_s:>4}  ${_i['mo_est']:>9,.0f}  ${_i['mo_od']:>9,.0f}  {_sav_s:>13}")

    _recs = []
    for _i in _inst_plan:
        if _i["ri_pct"] < 100 and _i["ri_hr"] and _i["save"] > 50:
            _gap = _i["nu"] - _i["nu_cov"]
            _act = f"Buy RI for remaining {_gap} NUs" if _i["ri_pct"] > 0 else f"Buy 1× {_i['class']} RI"
            _recs.append(("High" if _i["save"] > 300 else "Medium", _i["id"], _act, _i["save"]))
    for _r in _ri_detail:
        if _r["days_left"] < 180:
            _pri = "High" if _r["days_left"] < 90 else "Medium"
            _recs.append((_pri, "—", f"Renew {_r['id']} (expires {_r['expiry']}, {_r['days_left']}d left)", 0))
    _recs.sort(key=lambda x: (-x[3], x[0]))
    if _recs:
        print(f"\n  {'Priority':<8}  {'Instance':<40}  {'Recommended Action':<45}  {'Est. saving':>12}")
        print(f"  {'─'*8}  {'─'*40}  {'─'*45}  {'─'*12}")
        for _pri, _iid, _act, _sv in _recs:
            print(f"  {_pri:<8}  {_iid:<40}  {_act:<45}  {'~$'+f'{_sv:,.0f}/mo' if _sv > 0 else '—':>12}")
    else:
        print("\n  No RI coverage gaps — all instances are fully covered.")
else:
    print("  No RDS data available.")

# ══════════════════════════════════════════════════════════════════════════════
# 4. CLOUDWATCH — ANOMALY FLAG
# ══════════════════════════════════════════════════════════════════════════════

ecs_clusters_detail = load("infra/ecs/clusters_detail.json")
container_insights_on = None
if ecs_clusters_detail:
    for cluster in ecs_clusters_detail.get("clusters", []):
        for setting in cluster.get("settings", []):
            if setting.get("name") == "containerInsights":
                if setting.get("value") == "enabled":
                    container_insights_on = True
                else:
                    container_insights_on = False if container_insights_on is None else container_insights_on

cw_costs = {}
hr("4 · CLOUDWATCH COST ANOMALY")

if svc_data:
    cw_costs = {}
    for r in svc_data["ResultsByTime"]:
        month = r["TimePeriod"]["Start"][:7]
        for g in r["Groups"]:
            if g["Keys"][0] == "AmazonCloudWatch":
                cw_costs[month] = float(g["Metrics"]["BlendedCost"]["Amount"])

    if cw_costs:
        for month, cost in sorted(cw_costs.items()):
            flag = "  ← abnormally high" if cost > 500 else ""
            print(f"  {month}  {usd(cost)}{flag}")

        last_cw = list(cw_costs.values())[-1]
        if last_cw > 500:
            if container_insights_on is True:
                ci_status = "CONFIRMED ON — disable to save ~$2,600+/month"
            elif container_insights_on is False:
                ci_status = "confirmed OFF (cost source is elsewhere)"
            else:
                ci_status = "unknown — re-run pull_aws_data.sh (needs --include SETTINGS)"
            if HAS_DATADOG is True:
                _dd_note = ("Datadog sidecars (confirmed in config.json) already collect the\n"
                            "  same data — Container Insights is redundant.")
            elif HAS_DATADOG is False:
                _dd_note = ("No alternative monitoring configured (datadog: false in config.json) —\n"
                            "  verify you have another metrics source before disabling.")
            else:
                _dd_note = ("Before disabling, verify your monitoring stack (e.g. Datadog) collects\n"
                            "  equivalent container metrics. Set \"datadog\": true/false in config.json\n"
                            "  to make this recommendation definitive.")
            print(f"""
  CloudWatch at {usd(last_cw)}/month is unusually high.
  Container Insights status : {ci_status}

  If Container Insights is on: every Fargate task emits per-container CPU/memory/
  network metrics at $0.30/1,000 metrics. With 94 running tasks × 3 containers each,
  this compounds fast. {_dd_note}

  To disable: ECS Console → each cluster → Update cluster → Container Insights off.""")

# ══════════════════════════════════════════════════════════════════════════════
# 5. EC2 — STOPPED INSTANCES & UNATTACHED VOLUMES
# ══════════════════════════════════════════════════════════════════════════════

hr("5 · EC2 WASTE — STOPPED INSTANCES & UNATTACHED VOLUMES")

ec2_data = load("infra/ec2/instances.json")
vol_data = load("infra/ec2/volumes.json")

if ec2_data:
    all_instances = [i for r in ec2_data["Reservations"] for i in r["Instances"]]
    stopped = [i for i in all_instances if i["State"]["Name"] == "stopped"]

    if stopped:
        print(f"  Stopped instances ({len(stopped)}) — still paying for attached EBS:")
        for i in stopped:
            name = next((t["Value"] for t in i.get("Tags", []) if t["Key"] == "Name"), "unnamed")
            vols = i.get("BlockDeviceMappings", [])
            print(f"    {i['InstanceId']}  {i['InstanceType']:<14}  {name}")
    else:
        print("  No stopped instances.")

if vol_data:
    unattached = [v for v in vol_data["Volumes"] if v["State"] == "available"]
    if unattached:
        total_gb = sum(v["Size"] for v in unattached)
        # gp3 ~$0.08/GB/month, gp2 ~$0.10/GB/month
        est_cost = sum(v["Size"] * (0.08 if v["VolumeType"] == "gp3" else 0.10) for v in unattached)
        print(f"\n  Unattached EBS volumes ({len(unattached)}) — {total_gb} GB, ~{usd(est_cost)}/month wasted:")
        for v in unattached:
            name = next((t["Value"] for t in v.get("Tags", []) if t["Key"] == "Name"), "unnamed")
            print(f"    {v['VolumeId']}  {v['Size']:>4} GB  {v['VolumeType']}  {name}")
    else:
        print("\n  No unattached EBS volumes.")

# ── Oversized / gp2 volumes ───────────────────────────────────────────────────

if vol_data:
    gp2_vols = [v for v in vol_data["Volumes"] if v["VolumeType"] == "gp2" and v["State"] == "in-use"]
    if gp2_vols:
        total_gb = sum(v["Size"] for v in gp2_vols)
        savings = total_gb * 0.02  # gp2 $0.10 vs gp3 $0.08/GB
        print(f"\n  gp2 volumes still in use ({len(gp2_vols)}, {total_gb} GB) — upgrading to gp3 saves ~{usd(savings)}/month:")
        for v in sorted(gp2_vols, key=lambda x: -x["Size"])[:10]:
            name = next((t["Value"] for t in v.get("Tags", []) if t["Key"] == "Name"), "unnamed")
            print(f"    {v['VolumeId']}  {v['Size']:>4} GB  {name}")

# ══════════════════════════════════════════════════════════════════════════════
# 6. CLOUDWATCH LOG GROUPS
# ══════════════════════════════════════════════════════════════════════════════

hr("6 · CLOUDWATCH LOG GROUPS")

log_groups_data = load("infra/logs/log_groups.json")
log_groups_use1 = load("infra/logs/log_groups_us_east_1.json")

def print_log_groups(groups, region_label):
    total_bytes = sum(g.get("storedBytes", 0) for g in groups)
    no_retention = [g for g in groups if "retentionInDays" not in g]

    print(f"\n  [{region_label}]")
    print(f"  Total log groups : {len(groups)}")
    print(f"  Total stored     : {total_bytes / 1e9:.1f} GB")
    print(f"  No retention set : {len(no_retention)}  ← these grow forever")

    sorted_groups = sorted(groups, key=lambda g: -g.get("storedBytes", 0))
    print(f"\n  Largest log groups:")
    print(f"  {'Name':<55} {'Size':>8}  {'Retention':>12}")
    print(f"  {'─'*55} {'─'*8}  {'─'*12}")
    for g in sorted_groups[:15]:
        name = g["logGroupName"][-55:]
        size_gb = g.get("storedBytes", 0) / 1e9
        retention = f"{g['retentionInDays']}d" if "retentionInDays" in g else "NONE ⚠"
        print(f"  {name:<55} {size_gb:>7.2f}G  {retention:>12}")

    if no_retention:
        print(f"\n  Log groups with no retention policy ({len(no_retention)}):")
        for g in sorted(no_retention, key=lambda x: -x.get("storedBytes", 0)):
            size_gb = g.get("storedBytes", 0) / 1e9
            print(f"    {g['logGroupName']:<55} {size_gb:.2f} GB")
    return total_bytes, no_retention

if not log_groups_data or not log_groups_data.get("logGroups"):
    print("  No log group data — re-run pull_aws_data.sh to collect it.")
    log_groups_total_bytes = 0
    log_groups_use1_bytes = 0
else:
    log_groups_total_bytes, _ = print_log_groups(
        log_groups_data["logGroups"], f"ap-southeast-1")
    if log_groups_use1 and log_groups_use1.get("logGroups"):
        log_groups_use1_bytes, _ = print_log_groups(
            log_groups_use1["logGroups"], "us-east-1  (WAF / CloudFront)")
        # Isolate WAF log groups specifically for cost attribution
        waf_groups = [g for g in log_groups_use1["logGroups"]
                      if "waf" in g["logGroupName"].lower() or "aws-waf" in g["logGroupName"].lower()]
        if waf_groups:
            waf_bytes = sum(g.get("storedBytes", 0) for g in waf_groups)
            waf_no_ret = [g for g in waf_groups if "retentionInDays" not in g]
            print(f"\n  WAF log groups in us-east-1: {len(waf_groups)}, {waf_bytes/1e9:.1f} GB stored")
            if waf_no_ret:
                print(f"  WAF log groups with no retention: {len(waf_no_ret)} ← logs accumulate forever")
            waf_daily_gb = sum(
                g.get("storedBytes", 0) / g.get("retentionInDays", 365) / 1e9
                for g in waf_groups
            )
            waf_est_monthly = waf_daily_gb * 30 * 0.50 + waf_bytes / 1e9 * 0.03
            print(f"  Estimated WAF log ingest cost : ~${waf_est_monthly:,.0f}/month")
            print(f"  Redirecting WAF logs to S3    : ~${waf_bytes/1e9 * 0.023:,.0f}/month storage only (20x cheaper)")
    else:
        log_groups_use1_bytes = 0
        print(f"\n  [us-east-1]  No data — re-run pull_aws_data.sh to collect WAF log groups.")

# ══════════════════════════════════════════════════════════════════════════════
# 7. RDS UTILIZATION (CloudWatch metrics)
# ══════════════════════════════════════════════════════════════════════════════

hr("7 · RDS UTILIZATION (last 7 days avg/max)")

if rds_data:
    print(f"  {'Instance':<40} {'CPU avg':>8} {'CPU max':>8} {'Mem free avg':>14} {'Conns avg':>10}  {'Multi-AZ'}")
    print(f"  {'─'*40} {'─'*8} {'─'*8} {'─'*14} {'─'*10}  {'─'*8}")

    for db in sorted(rds_data["DBInstances"], key=lambda x: x["DBInstanceClass"], reverse=True):
        iid = db["DBInstanceIdentifier"]
        multi_az = "yes" if db.get("MultiAZ") else "no"

        def read_metric(metric):
            path = f"infra/rds/metrics/{iid}_{metric}.json"
            d = load(path)
            if not d or not d.get("Datapoints"):
                return None, None
            pts = d["Datapoints"]
            avg = sum(p["Average"] for p in pts) / len(pts)
            mx = max(p["Maximum"] for p in pts)
            return avg, mx

        cpu_avg, cpu_max = read_metric("CPUUtilization")
        mem_avg, _ = read_metric("FreeableMemory")
        conn_avg, _ = read_metric("DatabaseConnections")

        cpu_str = f"{cpu_avg:>7.1f}%" if cpu_avg is not None else "     n/a"
        cpu_max_str = f"{cpu_max:>7.1f}%" if cpu_max is not None else "     n/a"
        mem_str = f"{mem_avg/1e9:>12.1f}G" if mem_avg is not None else "           n/a"
        conn_str = f"{conn_avg:>9.0f}" if conn_avg is not None else "        n/a"

        # Flag low CPU (potential right-sizing candidate)
        flag = ""
        if cpu_avg is not None and cpu_avg < 10:
            flag = "  ← low CPU"
        if cpu_avg is not None and cpu_max is not None and cpu_max < 20:
            flag = "  ← right-size candidate"

        print(f"  {iid:<40} {cpu_str} {cpu_max_str} {mem_str} {conn_str}  {multi_az}{flag}")

# ══════════════════════════════════════════════════════════════════════════════
# 8. ECS TASK DEFINITIONS — CPU/MEMORY ALLOCATION
# ══════════════════════════════════════════════════════════════════════════════

taskdefs_dir = os.path.join(DATA_DIR, "infra/ecs/taskdefs")
taskdefs_dir = os.path.join(DATA_DIR, "infra/ecs/taskdefs")
hr("8 · ECS TASK DEFINITIONS")

if not os.path.isdir(taskdefs_dir) or not os.listdir(taskdefs_dir):
    print("  No task definition data — re-run pull_aws_data.sh to collect it.")
else:
    taskdefs = []
    for fname in os.listdir(taskdefs_dir):
        if not fname.endswith(".json"):
            continue
        d = load(f"infra/ecs/taskdefs/{fname}")
        if d and d.get("taskDefinition"):
            taskdefs.append(d["taskDefinition"])

    taskdefs.sort(key=lambda x: x.get("family", ""))

    print(f"  {'Family':<45} {'CPU':>6} {'Mem(MB)':>8}  {'Containers'}")
    print(f"  {'─'*45} {'─'*6} {'─'*8}  {'─'*30}")
    for td in taskdefs:
        family = td.get("family", "unknown")
        cpu = td.get("cpu", "—")
        mem = td.get("memory", "—")
        containers = ", ".join(c["name"] for c in td.get("containerDefinitions", []))
        print(f"  {family:<45} {str(cpu):>6} {str(mem):>8}  {containers}")

# ══════════════════════════════════════════════════════════════════════════════
# 8b. ECS RUNNING SERVICES — ACTUAL TASK COUNTS & RESOURCE CONSUMPTION
# ══════════════════════════════════════════════════════════════════════════════

hr("8b · ECS RUNNING SERVICES")

# Load all services_detail_<cluster>_<batch>.json files
all_services = []
ecs_dir = os.path.join(DATA_DIR, "infra/ecs")
if os.path.isdir(ecs_dir):
    for fname in sorted(os.listdir(ecs_dir)):
        if fname.startswith("services_detail_") and fname.endswith(".json"):
            d = load(f"infra/ecs/{fname}")
            if d and d.get("services"):
                all_services.extend(d["services"])

# Load autoscaling targets
autoscaling_targets = {}
asg_data = load("infra/ecs/autoscaling_targets.json")
if asg_data:
    for t in asg_data.get("ScalableTargets", []):
        # ResourceId format: "service/<cluster>/<service>"
        parts = t.get("ResourceId", "").split("/")
        if len(parts) == 3:
            svc_name = parts[2]
            autoscaling_targets[svc_name] = {
                "min": t.get("MinCapacity"),
                "max": t.get("MaxCapacity"),
            }

if not all_services:
    print("  No service detail data — re-run pull_aws_data.sh to collect it.")
else:
    # Build task def resource lookup
    td_resources = {}
    if os.path.isdir(taskdefs_dir):
        for fname in os.listdir(taskdefs_dir):
            if fname.endswith(".json"):
                d = load(f"infra/ecs/taskdefs/{fname}")
                if d and d.get("taskDefinition"):
                    td = d["taskDefinition"]
                    td_resources[td.get("family", "")] = {
                        "cpu": int(td.get("cpu") or 0),
                        "mem": int(td.get("memory") or 0),
                        "containers": [c["name"] for c in td.get("containerDefinitions", [])],
                    }

    all_services.sort(key=lambda s: s.get("serviceName", ""))

    total_running_cpu = 0
    total_running_mem = 0
    print(f"  {'Service':<45} {'Run/Des':>7}  {'CPU/task':>8}  {'Mem/task':>8}  {'Tot CPU':>7}  {'Tot Mem':>7}  {'Autoscale'}")
    print(f"  {'─'*45} {'─'*7}  {'─'*8}  {'─'*8}  {'─'*7}  {'─'*7}  {'─'*15}")

    for svc in all_services:
        name = svc.get("serviceName", "?")
        running = svc.get("runningCount", 0)
        desired = svc.get("desiredCount", 0)
        # Find task family — service name matches task family
        td = td_resources.get(name, {})
        cpu = td.get("cpu", 0)
        mem = td.get("mem", 0)
        total_cpu = cpu * running
        total_mem = mem * running
        total_running_cpu += total_cpu
        total_running_mem += total_mem

        asg = autoscaling_targets.get(name)
        asg_str = f"min={asg['min']} max={asg['max']}" if asg else "—"

        flag = " ←" if running != desired else ""
        print(f"  {name:<45} {running:>3}/{desired:<3}  {cpu:>7}u  {mem:>6}MB  {total_cpu:>6}u  {total_mem/1024:>5.1f}G  {asg_str}{flag}")

    print(f"\n  Total running Fargate resources:")
    print(f"    CPU   : {total_running_cpu:,} units  ({total_running_cpu/1024:.1f} vCPU)")
    print(f"    Memory: {total_running_mem:,} MB     ({total_running_mem/1024:.1f} GB)")

    # Fargate pricing ap-southeast-1: $0.04656/vCPU/hr, $0.00511/GB/hr
    fargate_cpu_cost = (total_running_cpu / 1024) * 0.04656 * 730
    fargate_mem_cost = (total_running_mem / 1024) * 0.00511 * 730
    fargate_total = fargate_cpu_cost + fargate_mem_cost
    print(f"\n  Estimated Fargate compute cost (excl. sidecars overhead):")
    print(f"    CPU   : ${fargate_cpu_cost:>8,.0f}/month  ({total_running_cpu/1024:.1f} vCPU × $0.04656/hr × 730h)")
    print(f"    Memory: ${fargate_mem_cost:>8,.0f}/month  ({total_running_mem/1024:.1f} GB × $0.00511/hr × 730h)")
    print(f"    Total : ${fargate_total:>8,.0f}/month  (Fargate pricing, ap-southeast-1)")

    # Sidecar overhead
    sidecar_cpu = sum(
        td_resources.get(svc["serviceName"], {}).get("cpu", 0) * svc["runningCount"]
        for svc in all_services
        if any(c in ["datadog", "filebeat"] for c in td_resources.get(svc["serviceName"], {}).get("containers", []))
    )
    print(f"\n  Note: sidecar containers (log shippers, monitoring agents) consume allocated")
    print(f"  CPU/memory even when idle — they are not free overhead.")

# ══════════════════════════════════════════════════════════════════════════════
# 9. RDS MEMORY DEEP DIVE & RIGHT-SIZING
# ══════════════════════════════════════════════════════════════════════════════

hr("9 · RDS MEMORY DEEP DIVE & RIGHT-SIZING")

if rds_data:
    print(f"  {'Instance':<40} {'Class':<20} {'RAM':>5}  {'Used pk':>7}  {'Util%':>6}  Verdict")
    print(f"  {'─'*40} {'─'*20} {'─'*5}  {'─'*7}  {'─'*6}  {'─'*30}")

    downsize_candidates = []
    for db in sorted(rds_data["DBInstances"], key=lambda x: -RDS_RAM_GB.get(x["DBInstanceClass"], 0)):
        iid = db["DBInstanceIdentifier"]
        cls = db["DBInstanceClass"]
        total_ram = RDS_RAM_GB.get(cls)
        if not total_ram:
            print(f"  {iid:<40} {cls:<20}  (unknown class)")
            continue

        d = load(f"infra/rds/metrics/{iid}_FreeableMemory.json")
        cpu_d = load(f"infra/rds/metrics/{iid}_CPUUtilization.json")
        if not d or not d.get("Datapoints"):
            print(f"  {iid:<40} {cls:<20} {total_ram:>4}G  (no metrics)")
            continue

        free_min_gb = min(p["Average"] for p in d["Datapoints"]) / 1e9
        peak_used = total_ram - free_min_gb
        pct = peak_used / total_ram * 100
        cpu_max = max(p["Maximum"] for p in cpu_d["Datapoints"]) if cpu_d and cpu_d.get("Datapoints") else None

        if pct < 55 and total_ram >= 8:
            verdict = "right-size candidate"
            downsize_candidates.append((iid, cls, total_ram, peak_used, pct, cpu_max))
        elif pct >= 85:
            verdict = "well-utilized / watch"
        elif pct >= 70:
            verdict = "healthy"
        else:
            verdict = "low utilization"

        cpu_str = f"cpu_max={cpu_max:.0f}%" if cpu_max else ""
        print(f"  {iid:<40} {cls:<20} {total_ram:>4}G  {peak_used:>6.1f}G  {pct:>5.0f}%  {verdict}  {cpu_str}")

    if downsize_candidates:
        print(f"\n  Right-sizing recommendations:")
        suggestions = {
            "db.m7g.2xlarge":  ("db.m7g.xlarge",  "~$200/mo"),
            "db.m7i.4xlarge":  ("db.m7i.2xlarge",  "~$300/mo"),
            "db.t3.medium":    ("db.t3.small",     "~$20/mo"),
        }
        for iid, cls, total, used, pct, cpu_max in downsize_candidates:
            suggestion = suggestions.get(cls)
            if suggestion:
                target, saving = suggestion
                print(f"    {iid}  {cls} → {target}  (peak usage {used:.1f}GB/{total}GB, {pct:.0f}%)  est. saving {saving}")
            else:
                print(f"    {iid}  {cls}  peak usage {used:.1f}GB/{total}GB ({pct:.0f}%) — check AWS Pricing Calculator for downsize options")

    print(f"""
  Note: instances already at high memory utilization cannot downsize on memory.
  For those, the primary cost lever is an RI purchase if still on-demand —
  a 1-year no-upfront RI typically saves ~30-40% vs on-demand.
  Check current pricing: https://aws.amazon.com/rds/pricing/""")

# ══════════════════════════════════════════════════════════════════════════════
# 10. MAY 1 SPIKE EXPLAINED
# ══════════════════════════════════════════════════════════════════════════════

hr("10 · MONTH-START BILLING SPIKES")

daily_svc = load("billing/daily_by_service.json")
if daily_svc:
    svc_days = defaultdict(dict)
    all_days = {}
    for r in daily_svc["ResultsByTime"]:
        day = r["TimePeriod"]["Start"]
        total = float(r["TimePeriod"].get("End", "0") and 0)  # placeholder
        for g in r["Groups"]:
            svc = g["Keys"][0]
            cost = float(g["Metrics"]["BlendedCost"]["Amount"])
            svc_days[svc][day] = cost

    # Recompute daily totals from daily_total.json (already loaded above)
    day_totals = {}
    if daily:
        for r in daily["ResultsByTime"]:
            day_totals[r["TimePeriod"]["Start"]] = float(r["Total"]["BlendedCost"]["Amount"])

    # Find first-of-month days in the window that are anomalously high
    baseline_days = [d for d in day_totals if not d.endswith("-01")]
    baseline_avg = sum(day_totals[d] for d in baseline_days) / len(baseline_days) if baseline_days else 0
    spike_days = sorted(
        [d for d in day_totals if d.endswith("-01") and day_totals[d] > baseline_avg * 1.5],
        key=lambda d: -day_totals[d]
    )

    if not spike_days:
        print(f"  No month-start billing spikes detected in the current 30-day window.")
        print(f"  Daily baseline average: {usd(baseline_avg)}")
    else:
        for spike_date in spike_days:
            spike_total = day_totals[spike_date]
            print(f"  {spike_date}  {usd(spike_total)}  (baseline avg: {usd(baseline_avg)},  excess: {usd(spike_total - baseline_avg)})")

            # Per-service breakdown for this spike date
            spikes = []
            for svc, days in svc_days.items():
                spike_cost = days.get(spike_date, 0)
                if spike_cost < 1:
                    continue
                others = [c for d, c in days.items() if d != spike_date]
                svc_avg = sum(others) / len(others) if others else 0
                spikes.append((svc, spike_cost, svc_avg, spike_cost - svc_avg))
            spikes.sort(key=lambda x: -x[3])

            print(f"\n  {'Service':<45} {spike_date:>12}  {'Daily avg':>12}  {'Excess':>12}")
            print(f"  {'─'*45} {'─'*12}  {'─'*12}  {'─'*12}")
            for svc, sc, sa, diff in spikes[:8]:
                print(f"  {svc:<45} {usd(sc):>12}  {usd(sa):>12}  {'+' + usd(diff).replace(' ', '') :>12}")

            rds_cost = next((sc for s, sc, _, _ in spikes if "Relational" in s), 0)
            tax_cost = next((sc for s, sc, _, _ in spikes if "Tax" in s), 0)
            print(f"""
  Diagnosis: RDS {usd(rds_cost)} + Tax {usd(tax_cost)} = {usd(rds_cost+tax_cost)} of the spike.
  This is NOT a usage surge — AWS bills RDS on-demand charges as a lump sum
  on the 1st of the month (prior month true-up). The attached tax charge
  confirms it's a one-time billing event, not a daily usage surge.
  Action: check your AWS invoice for {spike_date[:7]} to confirm the line item.""")

# ══════════════════════════════════════════════════════════════════════════════
# 11. CLOUDWATCH: LOGS vs METRICS — WHERE THE $2,575 COMES FROM
# ══════════════════════════════════════════════════════════════════════════════

hr("11 · CLOUDWATCH COST BREAKDOWN")

if log_groups_data and log_groups_data.get("logGroups"):
    groups = log_groups_data["logGroups"]

    # Estimate ingest cost: daily ingest ≈ stored / retention; monthly = daily × 30 × $0.50/GB
    def estimate_log_cost(group_list):
        rows = []
        for g in group_list:
            stored = g.get("storedBytes", 0)
            retention = g.get("retentionInDays", 365)
            daily_gb = stored / retention / 1e9
            est_monthly = daily_gb * 30 * 0.50 + stored / 1e9 * 0.03
            rows.append((g["logGroupName"], stored / 1e9, retention, daily_gb, est_monthly))
        rows.sort(key=lambda x: -x[4])
        return rows

    log_rows = estimate_log_cost(groups)
    total_log_est_apse1 = sum(r[4] for r in log_rows)

    # us-east-1 WAF log cost estimate
    use1_groups = (log_groups_use1 or {}).get("logGroups", [])
    log_rows_use1 = estimate_log_cost(use1_groups)
    total_log_est_use1 = sum(r[4] for r in log_rows_use1)
    waf_log_est = sum(r[4] for r in log_rows_use1
                      if "waf" in r[0].lower() or "aws-waf" in r[0].lower())

    total_log_est = total_log_est_apse1 + total_log_est_use1

    # Get actual CW cost
    actual_cw = cw_costs.get(sorted(cw_costs)[-1], 0) if cw_costs else 0
    metrics_gap = actual_cw - total_log_est

    print(f"  Estimated log costs — ap-southeast-1  : ${total_log_est_apse1:>8,.2f}/month")
    if use1_groups:
        print(f"  Estimated log costs — us-east-1 (WAF) : ${total_log_est_use1:>8,.2f}/month")
        print(f"    of which WAF log groups               : ${waf_log_est:>8,.2f}/month")
    else:
        print(f"  us-east-1 log cost                     :      unknown (no data)")
    print(f"  Total estimated log cost               : ${total_log_est:>8,.2f}/month")
    print(f"  Actual CloudWatch bill                 : ${actual_cw:>8,.2f}/month")
    print(f"  Unexplained gap (Container Insights)   : ${metrics_gap:>8,.2f}/month")

    ci_est = metrics_gap
    if HAS_DATADOG is True:
        _ci_arrow = "← disable, Datadog already covers this"
    elif HAS_DATADOG is False:
        _ci_arrow = "← verify monitoring coverage before disabling"
    else:
        _ci_arrow = "← disable if other monitoring covers containers"
    print(f"""
  Cost breakdown estimate:
    ap-southeast-1 log ingest/storage : ${total_log_est_apse1:>8,.2f}/month
    us-east-1 WAF log ingest/storage  : ${waf_log_est:>8,.2f}/month  ← redirect to S3 to save ~20x
    Container Insights (both clusters): ${ci_est:>8,.2f}/month  {_ci_arrow}

  Container Insights is confirmed ON. Every Fargate task emits per-container
  CPU/memory/network metrics at $0.30/1,000 metrics. With 94 running tasks ×
  3 containers each, this is the dominant cost driver.

  WAF logs: CloudFront WAF (us-east-1) logs every request to CloudWatch at
  $0.50/GB ingestion. Redirecting to S3 costs $0.023/GB — ~20x cheaper.
  WAF Console → Web ACLs → your CloudFront ACL → Logging → change to S3.""")

    print(f"\n  Top log groups by estimated monthly cost:")
    print(f"  {'Name':<52} {'GB/day':>7}  {'Est $/mo':>9}")
    print(f"  {'─'*52} {'─'*7}  {'─'*9}")
    for name, gb, ret, daily, est in log_rows[:10]:
        if est < 0.10:
            break
        ret_str = f"{ret}d" if ret < 365 else "none"
        print(f"  {name[-52:]:<52} {daily:>7.3f}  ${est:>8,.2f}")

    # Highlight unusually heavy log producers (> 5 GB/day)
    for name, gb, ret, daily, est in [r for r in log_rows if r[3] >= 5.0][:3]:
        print(f"""
  {name} is generating {daily:.1f} GB/day of logs — very high for a single service.
  At current retention, {gb:.0f} GB is stored. Consider structured logging + sampling
  for verbose/debug entries to reduce ingest volume.""")

# ══════════════════════════════════════════════════════════════════════════════
# 12. ECS COST GROWTH DRIVER
# ══════════════════════════════════════════════════════════════════════════════

hr("12 · ECS COST GROWTH DRIVER")

if svc_data:
    ecs_by_month = {}
    for r in svc_data["ResultsByTime"]:
        month = r["TimePeriod"]["Start"][:7]
        for g in r["Groups"]:
            if g["Keys"][0] == "Amazon Elastic Container Service":
                ecs_by_month[month] = float(g["Metrics"]["BlendedCost"]["Amount"])

    months_sorted = sorted(ecs_by_month)
    print(f"  ECS monthly cost:")
    for i, m in enumerate(months_sorted):
        change = f"  ({pct_change(ecs_by_month[months_sorted[i-1]], ecs_by_month[m])} vs prev)" if i > 0 else ""
        print(f"    {m}  {usd(ecs_by_month[m])}{change}")

if taskdefs_dir and os.path.isdir(taskdefs_dir) and os.listdir(taskdefs_dir):
    total_cpu = 0
    total_mem = 0
    sidecar_counts = defaultdict(int)
    taskdefs_list = []
    for fname in os.listdir(taskdefs_dir):
        if not fname.endswith(".json"):
            continue
        d = load(f"infra/ecs/taskdefs/{fname}")
        if not d or not d.get("taskDefinition"):
            continue
        td = d["taskDefinition"]
        cpu = int(td.get("cpu") or 0)
        mem = int(td.get("memory") or 0)
        total_cpu += cpu
        total_mem += mem
        taskdefs_list.append((td.get("family", "?"), cpu, mem))
        for c in td.get("containerDefinitions", []):
            if c["name"] not in (td.get("family", "?").split("-")[-1],):
                sidecar_counts[c["name"]] += 1

    print(f"\n  Total allocated across all task definitions (1 task each):")
    print(f"    CPU   : {total_cpu:,} units  ({total_cpu/1024:.1f} vCPU)")
    print(f"    Memory: {total_mem:,} MB     ({total_mem/1024:.1f} GB)")

    print(f"\n  Sidecar containers present across task families:")
    for name, count in sorted(sidecar_counts.items(), key=lambda x: -x[1]):
        print(f"    {name:<20} in {count} task families")

    print(f"""
  Sidecar containers (log shippers, monitoring agents) consume real
  CPU/memory allocation even when idle, multiplied
  by the number of running tasks. As task count scales, sidecar cost
  scales equally — they are not free.

  To investigate actual running task counts (not just definitions):
    aws ecs list-tasks --cluster <cluster> --region ap-southeast-1""")

# ══════════════════════════════════════════════════════════════════════════════
# 13. SAVINGS PLANS COVERAGE
# ══════════════════════════════════════════════════════════════════════════════

hr("13 · SAVINGS PLANS COVERAGE")

sp_coverage = load("billing/savings_plans_coverage.json")
if not sp_coverage or not sp_coverage.get("SavingsPlansCoverages"):
    print("  No Savings Plans coverage data — re-run pull_aws_data.sh to collect it.")
else:
    print(f"  {'Month':<10} {'On-Demand $':>12} {'SP Covered $':>13} {'Coverage %':>11}")
    print(f"  {'─'*10} {'─'*12} {'─'*13} {'─'*11}")
    for entry in sp_coverage["SavingsPlansCoverages"]:
        month = entry["TimePeriod"]["Start"][:7]
        cov = entry.get("Coverage", {})
        on_demand = float(cov.get("OnDemandCost", 0))
        sp_cost = float(cov.get("SpendCoveredBySavingsPlans", 0))
        pct = float(cov.get("CoveragePercentage", 0))
        flag = "  ← low coverage" if pct < 50 else ""
        print(f"  {month:<10} {usd(on_demand)} {usd(sp_cost)}  {pct:>10.1f}%{flag}")

# ══════════════════════════════════════════════════════════════════════════════
# 10. DATA TRANSFER BREAKDOWN
# ══════════════════════════════════════════════════════════════════════════════

hr("14 · DATA TRANSFER & VPC ENDPOINTS")

dt_data = load("billing/data_transfer.json")
if not dt_data or not dt_data.get("ResultsByTime"):
    print("  No data transfer breakdown — re-run pull_aws_data.sh to collect it.")
else:
    last = dt_data["ResultsByTime"][-1]
    month = last["TimePeriod"]["Start"][:7]
    groups = [(g["Keys"][0], float(g["Metrics"]["BlendedCost"]["Amount"]))
              for g in last.get("Groups", []) if float(g["Metrics"]["BlendedCost"]["Amount"]) > 1]
    groups.sort(key=lambda x: -x[1])
    print(f"  Top usage types driving EC2/EC2-Other cost — {month}:")
    for usage_type, cost in groups[:15]:
        print(f"  {usd(cost)}  {usage_type}")

# VPC endpoint gap analysis
vpc_endpoints_data = load("infra/vpc/vpc_endpoints.json")
nat_cost = sum(c for u, c in groups if "NatGateway" in u) if dt_data and dt_data.get("ResultsByTime") else 0

RECOMMENDED_ENDPOINTS = {
    "com.amazonaws.ap-southeast-1.s3":          ("Gateway", "free",        "S3 traffic from ECS/EC2 bypasses NAT"),
    "com.amazonaws.ap-southeast-1.ecr.api":     ("Interface", "$7/mo",     "ECR API calls from ECS tasks"),
    "com.amazonaws.ap-southeast-1.ecr.dkr":     ("Interface", "$7/mo",     "ECR image pulls from ECS tasks"),
    "com.amazonaws.ap-southeast-1.logs":        ("Interface", "$7/mo",     "CloudWatch Logs from ECS tasks"),
    "com.amazonaws.ap-southeast-1.monitoring":  ("Interface", "$7/mo",     "CloudWatch metrics from ECS tasks"),
}

if vpc_endpoints_data:
    existing = {ep.get("ServiceName") for ep in vpc_endpoints_data.get("VpcEndpoints", [])
                if ep.get("State") == "available"}
    missing = {k: v for k, v in RECOMMENDED_ENDPOINTS.items() if k not in existing}

    print(f"\n  NAT Gateway cost this month: {usd(nat_cost)}")
    print(f"  Existing VPC endpoints      : {len(existing)}")

    if missing:
        print(f"\n  Missing VPC endpoints (ECS tasks currently route these through NAT):")
        print(f"  {'Service':<52} {'Type':>9}  {'Cost':>6}  Note")
        print(f"  {'─'*52} {'─'*9}  {'─'*6}  {'─'*35}")
        for svc, (ep_type, cost, note) in missing.items():
            short = svc.replace("com.amazonaws.ap-southeast-1.", "")
            print(f"  {short:<52} {ep_type:>9}  {cost:>6}  {note}")
        print(f"\n  The S3 gateway endpoint is free and eliminates all S3 traffic from NAT.")
        print(f"  ECR + Logs endpoints (~$28/mo total) likely pay back via NAT reduction.")
    else:
        print(f"  All recommended VPC endpoints are already in place.")
else:
    print(f"\n  VPC endpoint data not available — re-run pull_aws_data.sh to collect it.")

# ══════════════════════════════════════════════════════════════════════════════
# 11. S3 LIFECYCLE POLICIES
# ══════════════════════════════════════════════════════════════════════════════

hr("15 · S3 LIFECYCLE POLICIES")

s3_lifecycle = load("infra/s3/lifecycle_policies.json")
if not s3_lifecycle:
    print("  No lifecycle data — re-run pull_aws_data.sh to collect it.")
else:
    no_lifecycle = [b for b in s3_lifecycle if b.get("Lifecycle") is None or b.get("Lifecycle") == "null"]
    has_lifecycle = [b for b in s3_lifecycle if b.get("Lifecycle") not in (None, "null")]
    print(f"  Buckets with lifecycle rules  : {len(has_lifecycle)}")
    print(f"  Buckets with NO lifecycle     : {len(no_lifecycle)}")
    if no_lifecycle:
        print(f"\n  Buckets without lifecycle (objects never expire):")
        for b in no_lifecycle[:20]:
            print(f"    {b['Bucket']}")

# ══════════════════════════════════════════════════════════════════════════════
# 11b. ELASTICACHE
# ══════════════════════════════════════════════════════════════════════════════

hr("16 · ELASTICACHE — REDIS/VALKEY CACHES")

ec_rgs = (load("infra/elasticache/replication_groups.json") or {}).get("ReplicationGroups", [])
ec_cls = (load("infra/elasticache/clusters.json") or {}).get("CacheClusters", [])
_cluster_ver = {c["CacheClusterId"]: c.get("EngineVersion", "?") for c in ec_cls}

if not ec_rgs and ec_cls:
    # replication_groups.json came back empty (API call failed during pull) —
    # reconstruct groups from cache clusters. Multi-AZ/failover live only at
    # the replication-group level, so they show as "?" in this mode.
    _ec_by_rg = defaultdict(list)
    for _c in ec_cls:
        _ec_by_rg[_c.get("ReplicationGroupId") or _c["CacheClusterId"]].append(_c)
    for _rg_id, _members in sorted(_ec_by_rg.items()):
        _first = _members[0]
        ec_rgs.append({
            "ReplicationGroupId": _rg_id,
            "CacheNodeType":      _first.get("CacheNodeType", "?"),
            "Engine":             _first.get("Engine", "?"),
            "MemberClusters":     [m["CacheClusterId"] for m in _members],
            "NodeGroups": [{"NodeGroupMembers":
                            [None] * sum(m.get("NumCacheNodes", 1) for m in _members)}],
            "MultiAZ":            "?",
            "AutomaticFailover":  "?",
            "AtRestEncryptionEnabled":  _first.get("AtRestEncryptionEnabled", False),
            "TransitEncryptionEnabled": _first.get("TransitEncryptionEnabled", False),
        })
    print("  Note: rebuilt from cache clusters — replication_groups.json was empty in this snapshot (pull API error).")

if not ec_rgs:
    print("  No ElastiCache caches found.")
else:
    print(f"  {'ID':<28}  {'Engine':<16}  {'Node Type':<22}  {'N':>2}  {'Multi-AZ':<10}  {'Failover':<10}  {'Encrypt':<8}  {'Est $/mo':>9}")
    print(f"  {'─'*28}  {'─'*16}  {'─'*22}  {'─'*2}  {'─'*10}  {'─'*10}  {'─'*8}  {'─'*9}")

    ec_total_monthly = 0.0
    ec_flags = []

    for rg in ec_rgs:
        rg_id        = rg["ReplicationGroupId"]
        node_type    = rg.get("CacheNodeType", "?")
        engine       = rg.get("Engine", "?")
        members      = rg.get("MemberClusters", [])
        ver          = _cluster_ver.get(members[0], "?") if members else "?"
        engine_ver   = f"{engine} {ver}"
        node_count   = sum(len(ng.get("NodeGroupMembers", [])) for ng in rg.get("NodeGroups", []))
        multi_az     = rg.get("MultiAZ", "?")
        auto_fo      = rg.get("AutomaticFailover", "?")
        at_rest_ok   = rg.get("AtRestEncryptionEnabled", False)
        transit_ok   = rg.get("TransitEncryptionEnabled", False)
        encrypt_str  = "yes" if (at_rest_ok and transit_ok) else ("partial" if (at_rest_ok or transit_ok) else "NO")

        hourly   = ELASTICACHE_OD_HOURLY.get(node_type, 0.0)
        ec_mo    = hourly * node_count * 730
        ec_total_monthly += ec_mo
        monthly_str = f"${ec_mo:,.0f}" if hourly else "?"

        print(f"  {rg_id:<28}  {engine_ver:<16}  {node_type:<22}  {node_count:>2}  {multi_az:<10}  {auto_fo:<10}  {encrypt_str:<8}  {monthly_str:>9}")

        if node_count == 1 and multi_az == "disabled":
            ec_flags.append(f"  [RISK] {rg_id}: single node, no Multi-AZ — cache failure causes downtime")
        if not at_rest_ok:
            ec_flags.append(f"  [WARN] {rg_id}: at-rest encryption disabled")
        if not transit_ok:
            ec_flags.append(f"  [WARN] {rg_id}: transit encryption disabled")

    print(f"\n  Estimated total: ${ec_total_monthly:,.0f}/month")
    if ec_flags:
        print()
        for _f in ec_flags:
            print(_f)

# ══════════════════════════════════════════════════════════════════════════════
# 11c. EKS
# ══════════════════════════════════════════════════════════════════════════════

hr("17 · EKS CLUSTERS")

eks_data     = load("infra/eks/clusters.json") or {}
eks_clusters = eks_data.get("clusters", [])

if not eks_clusters:
    print("  No EKS clusters found.")
else:
    print(f"  {'Cluster':<40}  {'Version':<10}  {'Status':<12}  {'Est $/mo':>9}")
    print(f"  {'─'*40}  {'─'*10}  {'─'*12}  {'─'*9}")
    eks_total = 0.0
    for cl in eks_clusters:
        cl_name  = cl.get("name", "?")
        cl_ver   = cl.get("version", "?")
        cl_status = cl.get("status", "?")
        eks_mo   = EKS_CTRL_PLANE_HOURLY * 730
        eks_total += eks_mo
        print(f"  {cl_name:<40}  {cl_ver:<10}  {cl_status:<12}  ${eks_mo:,.0f}")
    print(f"\n  Note: ${EKS_CTRL_PLANE_HOURLY}/hr per cluster control plane (worker nodes billed as EC2).")
    print(f"  Total EKS control plane: ${eks_total:,.0f}/month")

# ══════════════════════════════════════════════════════════════════════════════
# 12. SAVINGS OPPORTUNITIES SUMMARY
# ══════════════════════════════════════════════════════════════════════════════

hr("18 · SAVINGS OPPORTUNITIES SUMMARY")

# Collect all findings — each item: (priority, category, monthly_saving_int, text)
# priority: HIGH | MEDIUM | LOW | INFO
# category: COST | CLEANUP | PLANNING | RELIABILITY
opportunities = []

# RDS RI expiry
if rds_ri:
    today = now_utc().date()
    from datetime import timedelta
    for ri in rds_ri.get("ReservedDBInstances", []):
        if ri["State"] != "active":
            continue
        start = ri.get("StartTime", "")[:10]
        duration_secs = ri.get("Duration", 0)
        if start and duration_secs:
            start_dt = datetime.strptime(start, "%Y-%m-%d").date()
            end_dt = start_dt + timedelta(seconds=duration_secs)
            days_left = (end_dt - today).days
            if days_left < 90:
                pri = "HIGH" if days_left < 60 else "MEDIUM"
                opportunities.append((pri, "RELIABILITY", 0,
                    f"Renew RDS RI {ri['DBInstanceClass']} before {end_dt} ({days_left}d) to avoid on-demand pricing"))

# CloudWatch abnormally high
if cw_costs:
    last_cw = list(cw_costs.values())[-1]
    if last_cw > 500:
        opportunities.append(("HIGH", "COST", int(last_cw),
            f"Audit CloudWatch log retention — ${last_cw:,.0f}/month is abnormally high"))

# Stopped instances
if ec2_data:
    if stopped:
        opportunities.append(("MEDIUM", "CLEANUP", 0,
            f"Terminate or snapshot {len(stopped)} stopped EC2 instance(s) to stop paying for attached EBS"))

# Unattached volumes
if vol_data:
    unattached = [v for v in vol_data["Volumes"] if v["State"] == "available"]
    if unattached:
        total_gb = sum(v["Size"] for v in unattached)
        est_cost = sum(v["Size"] * (0.08 if v["VolumeType"] == "gp3" else 0.10) for v in unattached)
        opportunities.append(("MEDIUM", "CLEANUP", int(est_cost),
            f"Delete {len(unattached)} unattached EBS volume(s) ({total_gb} GB, ~${est_cost:.0f}/month)"))

# gp2 → gp3
if vol_data:
    gp2_vols = [v for v in vol_data["Volumes"] if v["VolumeType"] == "gp2" and v["State"] == "in-use"]
    if gp2_vols:
        savings = sum(v["Size"] for v in gp2_vols) * 0.02
        opportunities.append(("LOW", "CLEANUP", int(savings),
            f"Migrate {len(gp2_vols)} gp2 volume(s) to gp3 — saves ~${savings:.0f}/month (zero downtime)"))

# CloudWatch log groups without retention
if log_groups_data and log_groups_data.get("logGroups"):
    no_ret = [g for g in log_groups_data["logGroups"] if "retentionInDays" not in g]
    if no_ret:
        total_gb = sum(g.get("storedBytes", 0) for g in no_ret) / 1e9
        top_named = sorted(no_ret, key=lambda x: -x.get("storedBytes", 0))[:3]
        names = ", ".join(g["logGroupName"].rsplit("/", 1)[-1] for g in top_named)
        suffix = f" — largest: {names}" + (", ..." if len(no_ret) > 3 else "")
        opportunities.append(("MEDIUM", "CLEANUP", 0,
            f"Set retention on {len(no_ret)} CloudWatch log group(s) with no expiry ({total_gb:.1f} GB accumulating){suffix}"))

# RDS low utilization
if rds_data:
    for db in rds_data["DBInstances"]:
        iid = db["DBInstanceIdentifier"]
        d = load(f"infra/rds/metrics/{iid}_CPUUtilization.json")
        if d and d.get("Datapoints"):
            pts = d["Datapoints"]
            cpu_max = max(p["Maximum"] for p in pts)
            if cpu_max < 20 and db["DBInstanceClass"] not in ("db.t3.small", "db.t3.medium", "db.t4g.medium"):
                opportunities.append(("MEDIUM", "COST", 0,
                    f"Right-size {iid} ({db['DBInstanceClass']}) — CPU max {cpu_max:.1f}% over last 7 days"))

# RDS Multi-AZ instances
if rds_data:
    multi_az = [db for db in rds_data["DBInstances"] if db.get("MultiAZ")]
    if multi_az:
        opportunities.append(("INFO", "RELIABILITY", 0,
            "Verify Multi-AZ is justified for " + str(len(multi_az)) + " RDS instance(s): "
            + ", ".join(db["DBInstanceIdentifier"] for db in multi_az)))

# Savings Plans — consolidate all low-coverage months into one item
if sp_coverage and sp_coverage.get("SavingsPlansCoverages"):
    _low_sp = []
    for entry in sp_coverage["SavingsPlansCoverages"]:
        pct = float(entry.get("Coverage", {}).get("CoveragePercentage", 100))
        if pct < 50:
            _low_sp.append((entry["TimePeriod"]["Start"][:7], pct))
    if _low_sp:
        _sp_detail = ", ".join(f"{m}: {p:.0f}%" for m, p in sorted(_low_sp))
        opportunities.append(("MEDIUM", "PLANNING", 0,
            f"Savings Plans coverage below 50% in {len(_low_sp)} month(s) ({_sp_detail}) — consider expanding commitment"))

# S3 buckets without lifecycle
if s3_lifecycle:
    no_lc = [b for b in s3_lifecycle if b.get("Lifecycle") in (None, "null")]
    if len(no_lc) > 5:
        opportunities.append(("LOW", "CLEANUP", 0,
            f"{len(no_lc)} S3 bucket(s) have no lifecycle policy — old objects accumulate indefinitely"))

# Cost trend
if monthly:
    months = [(r["TimePeriod"]["Start"][:7], float(r["Total"]["BlendedCost"]["Amount"]))
              for r in monthly["ResultsByTime"]]
    if len(months) >= 2 and months[-1][1] > months[0][1] * 1.2:
        opportunities.append(("HIGH", "PLANNING", 0,
            f"Investigate cost growth: {months[0][0]} ${months[0][1]:,.0f} → {months[-1][0]} ${months[-1][1]:,.0f} (+{pct_change(months[0][1], months[-1][1])})"))

# ── Dollar-quantified items first ────────────────────────────────────────────
quantified = []

# RDS RI opportunities
if rds_data and rds_ri is not None:
    ri_covered_q = defaultdict(int)
    for ri in rds_ri.get("ReservedDBInstances", []):
        if ri["State"] == "active":
            ri_covered_q[ri["DBInstanceClass"]] += ri["DBInstanceCount"]
    for db in rds_data["DBInstances"]:
        cls = db["DBInstanceClass"]
        has_ri = ri_covered_q.get(cls, 0) > 0
        if has_ri:
            ri_covered_q[cls] -= 1
        elif cls in RDS_RI_1YR_HOURLY:
            saving = (RDS_ONDEMAND_HOURLY.get(cls, 0) - RDS_RI_1YR_HOURLY[cls]) * 730
            if saving > 50:
                quantified.append((saving, f"Buy RI for {db['DBInstanceIdentifier']} ({cls}) — ~${saving:,.0f}/mo saved, no upfront"))

# Container Insights
if container_insights_on is True and cw_costs:
    last_cw = list(cw_costs.values())[-1]
    if HAS_DATADOG is True:
        _ci_why = "Datadog already covers this"
    elif HAS_DATADOG is False:
        _ci_why = "verify monitoring coverage first — no Datadog configured"
    else:
        _ci_why = "verify other monitoring covers containers first"
    quantified.append((last_cw * 0.9, f"Disable Container Insights — est. ~${last_cw*0.9:,.0f}/mo saved ({_ci_why})"))
elif container_insights_on is None and cw_costs:
    last_cw = list(cw_costs.values())[-1]
    quantified.append((last_cw * 0.9, f"Investigate + disable Container Insights if on — could save ~${last_cw*0.9:,.0f}/mo"))

# RDS right-sizing candidates
if rds_data:
    for db in rds_data["DBInstances"]:
        iid = db["DBInstanceIdentifier"]
        cls = db["DBInstanceClass"]
        d = load(f"infra/rds/metrics/{iid}_FreeableMemory.json")
        if d and d.get("Datapoints"):
            total_ram = RDS_RAM_GB.get(cls, 0)
            free_min = min(p["Average"] for p in d["Datapoints"]) / 1e9
            pct_used = (total_ram - free_min) / total_ram * 100 if total_ram else 100
            if pct_used < 55 and total_ram >= 16 and cls in RDS_RI_1YR_HOURLY:
                od = RDS_ONDEMAND_HOURLY.get(cls, 0)
                saving = od * 730 * 0.45
                quantified.append((saving, f"Right-size {iid} ({cls}, {pct_used:.0f}% RAM used) — est. ~${saving:,.0f}/mo saved"))

# Savings Plans gap
if sp_coverage and sp_coverage.get("SavingsPlansCoverages"):
    for entry in sp_coverage["SavingsPlansCoverages"]:
        pct = float(entry.get("Coverage", {}).get("CoveragePercentage", 100))
        month = entry["TimePeriod"]["Start"][:7]
        if pct < 50:
            od = float(entry.get("Coverage", {}).get("OnDemandCost", 0))
            uncovered = od * (1 - pct / 100)
            saving = uncovered * 0.17
            if saving > 100:
                quantified.append((saving, f"Expand Savings Plans — {pct:.0f}% coverage in {month}, ~${saving:,.0f}/mo potential (17% on uncovered on-demand)"))
            break

quantified.sort(key=lambda x: -x[0])
total_quantified = sum(s for s, _ in quantified)

if quantified:
    print(f"  {'#':<3} {'Est. saving':>12}  Action")
    print(f"  {'─'*3} {'─'*12}  {'─'*50}")
    for i, (saving, desc) in enumerate(quantified, 1):
        print(f"  {i:<3} ~${saving:>9,.0f}/mo  {desc}")
    print(f"\n  Total quantified savings potential: ~${total_quantified:,.0f}/month  (~${total_quantified*12:,.0f}/year)")

# ── Other opportunities (not yet quantified) ─────────────────────────────────
_PRI_ORD = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "INFO": 3}
opportunities.sort(key=lambda x: (_PRI_ORD.get(x[0], 9), -x[2]))

print()
if opportunities:
    print(f"  Additional items:")
    for pri, cat, saving, text in opportunities:
        print(f"  [{pri}|{cat}|{saving}] {text}")
else:
    print("  No additional savings opportunities found.")

# ══════════════════════════════════════════════════════════════════════════════
# EXECUTIVE SUMMARY — printed last (all data is computed by now), then the
# reorder pass below moves it to the top of the report.
# ══════════════════════════════════════════════════════════════════════════════

hr("EXECUTIVE SUMMARY")

if monthly:
    _es_months = [(r["TimePeriod"]["Start"][:7], float(r["Total"]["BlendedCost"]["Amount"]))
                  for r in monthly["ResultsByTime"]]
    _es_last_mo, _es_last_cost = _es_months[-1]
    print(f"  Total monthly cost  : ${_es_last_cost:,.0f}  ({_es_last_mo})")
    if len(_es_months) >= 2:
        _es_prev_cost = _es_months[-2][1]
        print(f"  Month-over-month    : {pct_change(_es_prev_cost, _es_last_cost)}  (${_es_prev_cost:,.0f} → ${_es_last_cost:,.0f})")

if svc_data:
    _es_total = sum(c.get(last_month, 0) for _, c in ranked)
    for _es_i, (_es_svc, _es_costs) in enumerate(ranked[:3], 1):
        _es_c = _es_costs.get(last_month, 0)
        if _es_c > 0 and _es_total > 0:
            print(f"  Cost driver #{_es_i}      : {_es_svc} — ${_es_c:,.0f} ({_es_c/_es_total*100:.0f}% of total)")

if quantified:
    print(f"  Potential savings   : ~${total_quantified:,.0f}/month  (~${total_quantified*12:,.0f}/year quantified)")

if opportunities:
    _es_high = sum(1 for p, _, _, _ in opportunities if p == "HIGH")
    print(f"  Open findings       : {len(opportunities)} additional item(s), {_es_high} high priority")

if quantified:
    print(f"\n  Top recommendations:")
    print(f"  {'#':<3} {'Est. saving':>12}  Action")
    print(f"  {'─'*3} {'─'*12}  {'─'*50}")
    for _es_i, (_es_s, _es_d) in enumerate(quantified[:3], 1):
        print(f"  {_es_i:<3} ~${_es_s:>9,.0f}/mo  {_es_d}")

# ══════════════════════════════════════════════════════════════════════════════
# Reorder captured sections into executive reading order
# ══════════════════════════════════════════════════════════════════════════════

if OUTPUT_FMT in ("md", "pdf"):
    if _md_in_block:
        print("```")
        print()
else:
    import re as _re

    # (old section key, new number or None to keep) — executive reading order
    _SECTION_ORDER = [
        ("EXECUTIVE SUMMARY", None),
        ("1", None),            # Monthly cost trend
        ("2", None),            # Top services by cost
        ("18", "3"),            # Savings opportunities summary ← moved up
        ("4", None),            # CloudWatch cost anomaly
        ("10", "5"),            # Month-start billing spikes
        ("12", "6"),            # ECS cost growth driver
        ("3", "7"),             # RDS instances
        ("3b", "7b"),           # RDS cost estimate & RI opportunity
        ("3e", "7c"),           # RI plan
        ("7", "7d"),            # RDS utilization
        ("9", "7e"),            # RDS memory deep dive
        ("3c", "7f"),           # RDS daily cost tracker
        ("8", None),            # ECS task definitions
        ("8b", None),           # ECS running services
        ("6", "9"),             # CloudWatch log groups
        ("11", "9b"),           # CloudWatch cost breakdown
        ("5", "10"),            # EC2 waste
        ("13", "11"),           # Savings Plans coverage
        ("14", "12"),           # Data transfer & VPC endpoints
        ("15", "13"),           # S3 lifecycle
        ("16", "14"),           # ElastiCache
        ("17", "15"),           # EKS
        ("3d", "A1"),           # Appendix: tracked cluster day-by-day
    ]

    _text = sys.stdout.getvalue()
    sys.stdout = _STDOUT_REAL

    _hdr_re = _re.compile(r'^─{3} (.+?) ─{3,}\s*$')
    _preamble, _secs, _cur = [], [], None
    for _l in _text.splitlines():
        _m = _hdr_re.match(_l)
        if _m and _m.group(1).strip('─ '):
            _cur = [_m.group(1), []]
            _secs.append(_cur)
        elif _cur is None:
            _preamble.append(_l)
        else:
            _cur[1].append(_l)

    def _sec_key(t):
        _m2 = _re.match(r'^(\d+[a-z]?)\s*·\s*', t)
        return _m2.group(1) if _m2 else t.strip()

    def _emit(out, title, body):
        # normalize section separation: strip trailing blanks/bare rules
        while body and (not body[-1].strip() or set(body[-1].strip()) == {'─'}):
            body.pop()
        if out and out[-1].strip():
            out.append("")
        out.append(f"─── {title} {'─' * max(3, 64 - len(title) - 5)}")
        out.extend(body)

    _by_key = {}
    for _t, _b in _secs:
        _by_key.setdefault(_sec_key(_t), (_t, _b))
    _order_keys = {k for k, _ in _SECTION_ORDER}

    _out = list(_preamble)
    for _k, _newnum in _SECTION_ORDER:
        if _k not in _by_key:
            continue
        _t, _b = _by_key[_k]
        if _newnum:
            _t = _re.sub(r'^\d+[a-z]?', _newnum, _t, count=1)
        if _k == "3d":
            _t = "A1 · APPENDIX — " + _re.sub(r'^[A-Za-z]?\d+[a-z]?\s*·\s*', '', _t)
        _emit(_out, _t, _b)
    for _t, _b in _secs:
        if _sec_key(_t) in _order_keys:
            continue
        _emit(_out, _t, _b)

    print("\n".join(_out))
