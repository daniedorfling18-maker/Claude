from __future__ import annotations

import csv
import json
from pathlib import Path
from datetime import datetime, timezone
from statistics import mean

INP = Path("outputs/polymarket_training/tradable_pseudo_validation_snapshots.csv")
OUT = Path("outputs/polymarket_training/tradable_pseudo_validation_latest.csv")
SUMMARY = Path("outputs/polymarket_model_governance/tradable_pseudo_validation_latest_summary.json")

def parse_dt(x):
    if not x:
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(str(x).replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return datetime.min.replace(tzinfo=timezone.utc)

def fnum(x):
    try:
        if x is None or x == "":
            return None
        return float(x)
    except Exception:
        return None

with INP.open("r", encoding="utf-8-sig", errors="replace", newline="") as f:
    rows = list(csv.DictReader(f))

latest = {}

for i, r in enumerate(rows):
    key = (
        r.get("pseudo_market_slug", ""),
        r.get("token_id", ""),
    )
    ts = parse_dt(r.get("snapshot_timestamp", ""))
    old = latest.get(key)

    if old is None:
        latest[key] = (ts, i, r)
    else:
        old_ts, old_i, _ = old
        if ts > old_ts or (ts == old_ts and i > old_i):
            latest[key] = (ts, i, r)

latest_rows = [v[2] for v in latest.values()]

OUT.parent.mkdir(parents=True, exist_ok=True)
SUMMARY.parent.mkdir(parents=True, exist_ok=True)

if latest_rows:
    with OUT.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(latest_rows[0].keys()), extrasaction="ignore")
        w.writeheader()
        w.writerows(latest_rows)
else:
    OUT.write_text("", encoding="utf-8")

briers = [fnum(r.get("pseudo_brier")) for r in latest_rows]
briers = [x for x in briers if x is not None]

summary = {
    "input_snapshot_rows": len(rows),
    "latest_rows": len(latest_rows),
    "latest_tokens": len({r.get("token_id") for r in latest_rows}),
    "latest_markets": len({r.get("pseudo_market_slug") for r in latest_rows}),
    "mean_latest_pseudo_brier": mean(briers) if briers else None,
    "output": str(OUT),
    "note": "Latest row per market/token from tradable pseudo-validation snapshots.",
}

SUMMARY.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
print(json.dumps(summary, indent=2, sort_keys=True))
