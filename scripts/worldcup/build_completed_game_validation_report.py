from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from datetime import datetime, timezone
from statistics import mean

HISTORY = Path("outputs/polymarket_training/completed_game_clob_price_history_2026_06_23.csv")
LABELS = Path("outputs/polymarket_training/completed_game_labels_2026_06_23.csv")

OUT = Path("outputs/polymarket_training/completed_game_validation_2026_06_23.csv")
SUMMARY = Path("outputs/polymarket_model_governance/completed_game_validation_2026_06_23_summary.json")

KICKOFF_UTC = datetime.fromisoformat("2026-06-23T20:00:00+00:00")

def read_csv(path: Path):
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as f:
        return list(csv.DictReader(f))

def fnum(x):
    try:
        if x is None or x == "":
            return None
        return float(x)
    except Exception:
        return None

def parse_ts(x):
    if x is None or x == "":
        return None
    try:
        value = float(x)
        # CLOB history usually returns epoch seconds; handle milliseconds just in case.
        if value > 10_000_000_000:
            value = value / 1000
        return datetime.fromtimestamp(value, timezone.utc)
    except Exception:
        try:
            return datetime.fromisoformat(str(x).replace("Z", "+00:00")).astimezone(timezone.utc)
        except Exception:
            return None

def clip(p, eps=1e-6):
    return min(max(p, eps), 1 - eps)

def brier(p, target):
    if p is None or target is None:
        return None
    return (p - target) ** 2

def log_loss(p, target):
    if p is None or target is None:
        return None
    p = clip(p)
    return -(target * math.log(p) + (1 - target) * math.log(1 - p))

def hit(p, target):
    if p is None or target is None:
        return None
    return int((p >= 0.5) == (target == 1))

def snapshot_metrics(prefix, snap, target):
    if snap is None:
        return {
            f"{prefix}_timestamp_utc": "",
            f"{prefix}_price": "",
            f"{prefix}_brier": "",
            f"{prefix}_log_loss": "",
            f"{prefix}_class_hit": "",
        }

    price = fnum(snap.get("history_price"))
    ts = parse_ts(snap.get("history_timestamp"))

    return {
        f"{prefix}_timestamp_utc": ts.isoformat() if ts else "",
        f"{prefix}_price": price,
        f"{prefix}_brier": brier(price, target),
        f"{prefix}_log_loss": log_loss(price, target),
        f"{prefix}_class_hit": hit(price, target),
    }

labels = read_csv(LABELS)
history = read_csv(HISTORY)

labels_by_token = {r["token_id"]: r for r in labels}

by_token = {}
for r in history:
    token = r.get("token_id", "")
    if token not in labels_by_token:
        continue

    ts = parse_ts(r.get("history_timestamp"))
    price = fnum(r.get("history_price"))

    if ts is None or price is None:
        continue

    r["_ts"] = ts
    r["_price"] = price
    by_token.setdefault(token, []).append(r)

report = []

for token, label in labels_by_token.items():
    rows = sorted(by_token.get(token, []), key=lambda r: r["_ts"])

    target = fnum(label.get("target"))

    first_snap = rows[0] if rows else None
    pre_kickoff_rows = [r for r in rows if r["_ts"] <= KICKOFF_UTC]
    last_pre_kickoff = pre_kickoff_rows[-1] if pre_kickoff_rows else None
    latest_snap = rows[-1] if rows else None

    out = {
        "fixture": label.get("fixture", ""),
        "fixture_slug": label.get("fixture_slug", ""),
        "final_score": label.get("final_score", ""),
        "market_type": label.get("market_type", ""),
        "market_question": label.get("market_question", ""),
        "outcome": label.get("outcome", ""),
        "token_id": token,
        "target": label.get("target", ""),
        "resolved_outcome_price": label.get("resolved_outcome_price", ""),
        "history_rows": len(rows),
        "pre_kickoff_rows": len(pre_kickoff_rows),
        "kickoff_utc": KICKOFF_UTC.isoformat(),
    }

    out.update(snapshot_metrics("first", first_snap, target))
    out.update(snapshot_metrics("last_pre_kickoff", last_pre_kickoff, target))
    out.update(snapshot_metrics("latest", latest_snap, target))

    report.append(out)

OUT.parent.mkdir(parents=True, exist_ok=True)
SUMMARY.parent.mkdir(parents=True, exist_ok=True)

fieldnames = [
    "fixture",
    "fixture_slug",
    "final_score",
    "market_type",
    "market_question",
    "outcome",
    "token_id",
    "target",
    "resolved_outcome_price",
    "history_rows",
    "pre_kickoff_rows",
    "kickoff_utc",
    "first_timestamp_utc",
    "first_price",
    "first_brier",
    "first_log_loss",
    "first_class_hit",
    "last_pre_kickoff_timestamp_utc",
    "last_pre_kickoff_price",
    "last_pre_kickoff_brier",
    "last_pre_kickoff_log_loss",
    "last_pre_kickoff_class_hit",
    "latest_timestamp_utc",
    "latest_price",
    "latest_brier",
    "latest_log_loss",
    "latest_class_hit",
]

with OUT.open("w", encoding="utf-8", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(report)

def metric(name):
    xs = [fnum(r.get(name)) for r in report]
    xs = [x for x in xs if x is not None]
    return mean(xs) if xs else None

def hit_rate(name):
    xs = [fnum(r.get(name)) for r in report]
    xs = [x for x in xs if x is not None]
    return mean(xs) if xs else None

summary = {
    "fixture": "england_ghana",
    "final_score": "England 0-0 Ghana",
    "label_tokens": len(labels_by_token),
    "tokens_with_history": len(by_token),
    "validation_rows": len(report),
    "kickoff_utc": KICKOFF_UTC.isoformat(),
    "mean_first_brier": metric("first_brier"),
    "mean_last_pre_kickoff_brier": metric("last_pre_kickoff_brier"),
    "mean_latest_brier": metric("latest_brier"),
    "first_hit_rate": hit_rate("first_class_hit"),
    "last_pre_kickoff_hit_rate": hit_rate("last_pre_kickoff_class_hit"),
    "latest_hit_rate": hit_rate("latest_class_hit"),
    "output": str(OUT),
    "interpretation": "Lower Brier is better. Class hit uses 0.5 threshold on token probability.",
}

SUMMARY.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
print(json.dumps(summary, indent=2, sort_keys=True))
