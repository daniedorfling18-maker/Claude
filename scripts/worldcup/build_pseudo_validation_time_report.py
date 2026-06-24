from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from datetime import datetime, timezone
from statistics import mean

INP = Path("outputs/polymarket_training/pseudo_validation_snapshots.csv")
OUT = Path("outputs/polymarket_training/pseudo_validation_time_report.csv")
SUMMARY = Path("outputs/polymarket_model_governance/pseudo_validation_time_report_summary.json")

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

def clip(p, eps=1e-6):
    return min(max(p, eps), 1.0 - eps)

def market_prob(row):
    midpoint = fnum(row.get("midpoint"))
    if midpoint is not None:
        return midpoint

    bid = fnum(row.get("best_bid"))
    ask = fnum(row.get("best_ask"))
    if bid is not None and ask is not None:
        return (bid + ask) / 2

    return fnum(row.get("price"))

def brier(p, target):
    if p is None or target is None:
        return None
    return (p - target) ** 2

def log_loss(p, target):
    if p is None or target is None:
        return None
    p = clip(p)
    return -(target * math.log(p) + (1 - target) * math.log(1 - p))

def snapshot_payload(label, row):
    p = market_prob(row)
    target = fnum(row.get("pseudo_target"))
    return {
        f"{label}_timestamp": row.get("snapshot_timestamp", ""),
        f"{label}_probability": p,
        f"{label}_brier": brier(p, target),
        f"{label}_log_loss": log_loss(p, target),
    }

with INP.open("r", encoding="utf-8-sig", errors="replace", newline="") as f:
    rows = list(csv.DictReader(f))

by_token = {}
for i, r in enumerate(rows):
    key = (
        r.get("pseudo_market_slug", ""),
        r.get("token_id", ""),
    )
    r["_row_index"] = i
    r["_ts"] = parse_dt(r.get("snapshot_timestamp", ""))
    by_token.setdefault(key, []).append(r)

report = []

for (slug, token), group in by_token.items():
    group = sorted(group, key=lambda r: (r["_ts"], r["_row_index"]))

    first = group[0]
    latest = group[-1]

    tradable = []
    uncertain = []

    for r in group:
        p = market_prob(r)
        if p is None:
            continue
        if 0.02 <= p <= 0.98:
            tradable.append(r)
        if 0.05 <= p <= 0.95:
            uncertain.append(r)

    first_tradable = tradable[0] if tradable else ""
    first_uncertain = uncertain[0] if uncertain else ""

    base = {
        "market_slug": slug,
        "token_id": token,
        "question": latest.get("pseudo_question", ""),
        "outcome": latest.get("pseudo_outcome", ""),
        "target": latest.get("pseudo_target", ""),
        "pseudo_terminal_probability": latest.get("pseudo_outcome_price", ""),
        "pseudo_confidence": latest.get("pseudo_confidence", ""),
        "snapshot_count": len(group),
        "has_tradable_snapshot": bool(first_tradable),
        "has_uncertain_snapshot": bool(first_uncertain),
    }

    base.update(snapshot_payload("first", first))

    if first_tradable:
        base.update(snapshot_payload("first_tradable", first_tradable))
    else:
        base.update({
            "first_tradable_timestamp": "",
            "first_tradable_probability": "",
            "first_tradable_brier": "",
            "first_tradable_log_loss": "",
        })

    if first_uncertain:
        base.update(snapshot_payload("first_uncertain", first_uncertain))
    else:
        base.update({
            "first_uncertain_timestamp": "",
            "first_uncertain_probability": "",
            "first_uncertain_brier": "",
            "first_uncertain_log_loss": "",
        })

    base.update(snapshot_payload("latest", latest))

    first_p = fnum(base.get("first_probability"))
    latest_p = fnum(base.get("latest_probability"))
    target = fnum(base.get("target"))

    base["probability_move_first_to_latest"] = "" if first_p is None or latest_p is None else latest_p - first_p
    base["moved_toward_target"] = "" if first_p is None or latest_p is None or target is None else abs(latest_p - target) < abs(first_p - target)

    report.append(base)

OUT.parent.mkdir(parents=True, exist_ok=True)
SUMMARY.parent.mkdir(parents=True, exist_ok=True)

fieldnames = [
    "market_slug",
    "token_id",
    "question",
    "outcome",
    "target",
    "pseudo_terminal_probability",
    "pseudo_confidence",
    "snapshot_count",
    "has_tradable_snapshot",
    "has_uncertain_snapshot",
    "first_timestamp",
    "first_probability",
    "first_brier",
    "first_log_loss",
    "first_tradable_timestamp",
    "first_tradable_probability",
    "first_tradable_brier",
    "first_tradable_log_loss",
    "first_uncertain_timestamp",
    "first_uncertain_probability",
    "first_uncertain_brier",
    "first_uncertain_log_loss",
    "latest_timestamp",
    "latest_probability",
    "latest_brier",
    "latest_log_loss",
    "probability_move_first_to_latest",
    "moved_toward_target",
]

with OUT.open("w", encoding="utf-8", newline="") as f:
    w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
    w.writeheader()
    w.writerows(report)

def metric(name):
    xs = [fnum(r.get(name)) for r in report]
    xs = [x for x in xs if x is not None]
    return mean(xs) if xs else None

summary = {
    "input_snapshot_rows": len(rows),
    "token_rows": len(report),
    "market_count": len({r["market_slug"] for r in report}),
    "rows_with_tradable_snapshot": sum(1 for r in report if r["has_tradable_snapshot"]),
    "rows_with_uncertain_snapshot": sum(1 for r in report if r["has_uncertain_snapshot"]),
    "mean_first_brier": metric("first_brier"),
    "mean_first_tradable_brier": metric("first_tradable_brier"),
    "mean_first_uncertain_brier": metric("first_uncertain_brier"),
    "mean_latest_brier": metric("latest_brier"),
    "moved_toward_target_count": sum(1 for r in report if str(r.get("moved_toward_target")) == "True"),
    "output": str(OUT),
    "warning": "Pseudo labels are near-terminal active-market labels. Use for shadow validation only.",
}

SUMMARY.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
print(json.dumps(summary, indent=2, sort_keys=True))
