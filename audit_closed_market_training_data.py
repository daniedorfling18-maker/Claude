from __future__ import annotations

import csv
from pathlib import Path
from datetime import datetime, timezone
from collections import Counter, defaultdict

ROOT = Path("outputs")

def parse_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None

def read_csv(path):
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))

def token(row):
    return (
        row.get("token_id")
        or row.get("asset_id")
        or row.get("clob_token_id")
        or row.get("token")
        or ""
    ).strip()

def market(row):
    return (
        row.get("market_slug")
        or row.get("market")
        or row.get("condition_id")
        or row.get("gamma_market_id")
        or row.get("question_id")
        or ""
    ).strip()

def outcome(row):
    return (row.get("outcome") or row.get("outcome_name") or "").strip()

# 1. Load known resolutions
resolution_files = [
    ROOT / "polymarket_training" / "market_resolutions.csv",
    ROOT / "polymarket_training" / "historical_resolutions.csv",
    ROOT / "polymarket_training" / "websocket_resolutions.csv",
]

resolutions = []
for p in resolution_files:
    rows = read_csv(p)
    for r in rows:
        r["_file"] = str(p)
        resolutions.append(r)

clean_resolutions = [
    r for r in resolutions
    if (r.get("resolution_quality") or "").strip() == "clean_settlement"
]

res_by_token = {}
for r in clean_resolutions:
    t = token(r)
    if t:
        res_by_token[t] = r

print("RESOLUTION AUDIT")
print("-" * 80)
print(f"resolution_rows_total: {len(resolutions)}")
print(f"clean_resolution_rows: {len(clean_resolutions)}")
print(f"clean_resolution_tokens: {len(res_by_token)}")
print("resolution_quality_counts:", Counter((r.get("resolution_quality") or "").strip() for r in resolutions))

# 2. Load all possible feature/snapshot files
snapshot_files = list(ROOT.rglob("*features*.csv")) + list(ROOT.rglob("*snapshots*.csv"))
snapshot_files = sorted(set(snapshot_files))

print("\nSNAPSHOT FILES")
print("-" * 80)
for p in snapshot_files:
    try:
        rows = read_csv(p)
        print(f"{p} | rows={len(rows)}")
    except Exception as e:
        print(f"{p} | ERROR {e}")

# 3. Search for rows that join to clean resolutions
joined = []
join_counts_by_file = Counter()

for p in snapshot_files:
    try:
        rows = read_csv(p)
    except Exception:
        continue

    for r in rows:
        t = token(r)
        if not t or t not in res_by_token:
            continue

        res = res_by_token[t]

        snapshot_time = parse_dt(
            r.get("collected_at_utc")
            or r.get("source_timestamp")
            or r.get("snapshot_time")
            or r.get("created_at")
            or r.get("updated_at")
        )

        close_time = parse_dt(
            res.get("close_time")
            or res.get("end_time")
            or res.get("resolved_at")
            or res.get("resolution_time")
        )

        # If we cannot prove snapshot was before close, mark risky.
        if snapshot_time and close_time:
            leakage_status = "pre_close_ok" if snapshot_time < close_time else "post_close_leakage_risk"
        else:
            leakage_status = "unknown_time_risk"

        joined.append({
            "snapshot_file": str(p),
            "token_id": t,
            "market_snapshot": market(r),
            "market_resolution": market(res),
            "outcome_snapshot": outcome(r),
            "outcome_resolution": outcome(res),
            "target": res.get("target"),
            "snapshot_time": snapshot_time.isoformat() if snapshot_time else "",
            "close_time": close_time.isoformat() if close_time else "",
            "leakage_status": leakage_status,
            "midpoint": r.get("midpoint") or "",
            "best_bid": r.get("best_bid") or "",
            "best_ask": r.get("best_ask") or "",
        })
        join_counts_by_file[str(p)] += 1

print("\nJOIN AUDIT")
print("-" * 80)
print(f"joined_rows_total: {len(joined)}")
print("leakage_status_counts:", Counter(j["leakage_status"] for j in joined))
print("join_counts_by_file:")
for k, v in join_counts_by_file.most_common():
    print(f"  {v:>8}  {k}")

# 4. Write audit rows
out = ROOT / "polymarket_model_governance" / "closed_market_training_join_audit.csv"
out.parent.mkdir(parents=True, exist_ok=True)

fieldnames = [
    "snapshot_file",
    "token_id",
    "market_snapshot",
    "market_resolution",
    "outcome_snapshot",
    "outcome_resolution",
    "target",
    "snapshot_time",
    "close_time",
    "leakage_status",
    "midpoint",
    "best_bid",
    "best_ask",
]

with out.open("w", encoding="utf-8", newline="") as f:
    w = csv.DictWriter(f, fieldnames=fieldnames)
    w.writeheader()
    w.writerows(joined)

print(f"\nWrote: {out}")
