from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from datetime import datetime, timezone

HISTORY = Path("outputs/polymarket_training/completed_game_clob_price_history_2026_06_23.csv")
OUT = Path("outputs/polymarket_training/england_ghana_moneyline_validation_2026_06_23.csv")
SUMMARY = Path("outputs/polymarket_model_governance/england_ghana_moneyline_validation_2026_06_23_summary.json")

KICKOFF_UTC = datetime.fromisoformat("2026-06-23T20:00:00+00:00")

# For moneyline view, use the YES tokens only:
# England win Yes, Draw Yes, Ghana win Yes.
YES_TOKEN_MAP = {
    "England": {
        "token_id": "63611572432092712507452794046269856873324193737948513078896856667745770559698",
        "target": 0,
    },
    "Draw": {
        "token_id": "113575947344702991323223447752604645066262694843576298874524902811228579544185",
        "target": 1,
    },
    "Ghana": {
        "token_id": "111239697803361919523803068759725643725914563280609261384832780529311817077235",
        "target": 0,
    },
}

def read_csv(path):
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
    try:
        value = float(x)
        if value > 10_000_000_000:
            value = value / 1000
        return datetime.fromtimestamp(value, timezone.utc)
    except Exception:
        return None

def brier_multiclass(probs, targets):
    return sum((probs[k] - targets[k]) ** 2 for k in probs)

history = read_csv(HISTORY)

by_token = {}
for r in history:
    token = r.get("token_id", "")
    ts = parse_ts(r.get("history_timestamp"))
    price = fnum(r.get("history_price"))

    if token and ts and price is not None:
        by_token.setdefault(token, []).append({
            "timestamp": ts,
            "price": price,
        })

for token in by_token:
    by_token[token].sort(key=lambda r: r["timestamp"])

def pick_price(token_id, mode):
    rows = by_token.get(token_id, [])
    if not rows:
        return None

    if mode == "first":
        return rows[0]

    if mode == "last_pre_kickoff":
        pre = [r for r in rows if r["timestamp"] <= KICKOFF_UTC]
        return pre[-1] if pre else None

    if mode == "latest":
        return rows[-1]

    return None

report = []

for mode in ["first", "last_pre_kickoff", "latest"]:
    raw_probs = {}
    timestamps = {}

    for outcome, meta in YES_TOKEN_MAP.items():
        snap = pick_price(meta["token_id"], mode)
        if snap is None:
            raw_probs[outcome] = None
            timestamps[outcome] = ""
        else:
            raw_probs[outcome] = snap["price"]
            timestamps[outcome] = snap["timestamp"].isoformat()

    available = {k: v for k, v in raw_probs.items() if v is not None}
    total = sum(available.values()) if available else None

    if total and total > 0:
        norm_probs = {k: available[k] / total for k in available}
    else:
        norm_probs = {}

    targets = {
        "England": 0,
        "Draw": 1,
        "Ghana": 0,
    }

    predicted_outcome = max(norm_probs, key=norm_probs.get) if norm_probs else ""
    actual_outcome = "Draw"

    brier_raw = ""
    brier_norm = ""

    if len(available) == 3:
        brier_raw = brier_multiclass(available, targets)
        brier_norm = brier_multiclass(norm_probs, targets)

    report.append({
        "snapshot": mode,
        "kickoff_utc": KICKOFF_UTC.isoformat(),
        "england_price_raw": raw_probs.get("England"),
        "draw_price_raw": raw_probs.get("Draw"),
        "ghana_price_raw": raw_probs.get("Ghana"),
        "raw_probability_sum": total,
        "england_probability_normalised": norm_probs.get("England", ""),
        "draw_probability_normalised": norm_probs.get("Draw", ""),
        "ghana_probability_normalised": norm_probs.get("Ghana", ""),
        "predicted_outcome": predicted_outcome,
        "actual_outcome": actual_outcome,
        "prediction_correct": predicted_outcome == actual_outcome,
        "brier_raw": brier_raw,
        "brier_normalised": brier_norm,
        "england_timestamp": timestamps.get("England", ""),
        "draw_timestamp": timestamps.get("Draw", ""),
        "ghana_timestamp": timestamps.get("Ghana", ""),
    })

OUT.parent.mkdir(parents=True, exist_ok=True)
SUMMARY.parent.mkdir(parents=True, exist_ok=True)

with OUT.open("w", encoding="utf-8", newline="") as f:
    fieldnames = list(report[0].keys())
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(report)

summary = {
    "fixture": "england_ghana",
    "final_score": "England 0-0 Ghana",
    "actual_outcome": "Draw",
    "rows": len(report),
    "output": str(OUT),
    "latest_prediction_correct": report[-1]["prediction_correct"] if report else None,
    "last_pre_kickoff_prediction_correct": next((r["prediction_correct"] for r in report if r["snapshot"] == "last_pre_kickoff"), None),
    "note": "Moneyline view uses YES tokens for England win, Draw, and Ghana win, then normalises probabilities across the three outcomes.",
}

SUMMARY.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
print(json.dumps(summary, indent=2, sort_keys=True))
