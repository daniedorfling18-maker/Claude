from __future__ import annotations

import csv
import json
from pathlib import Path
from datetime import datetime, timezone

HISTORY = Path("outputs/polymarket_training/worldcup_historical_clob_moneyline_price_history_closed_only.csv")
LABELS = Path("outputs/polymarket_training/worldcup_historical_clob_moneyline_labels_closed_only.csv")

OUT_TOKEN = Path("outputs/polymarket_training/worldcup_historical_clob_token_validation_closed_only.csv")
OUT_MONEYLINE = Path("outputs/polymarket_training/worldcup_historical_clob_moneyline_validation_closed_only.csv")
SUMMARY = Path("outputs/polymarket_model_governance/worldcup_historical_clob_validation_closed_only_summary.json")

def read_csv(path):
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as f:
        return list(csv.DictReader(f))

def write_csv(path, rows, fieldnames):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

def fnum(x):
    try:
        if x is None or x == "":
            return None
        return float(x)
    except Exception:
        return None

history = read_csv(HISTORY)
labels = read_csv(LABELS)

labels_by_token = {r["token_id"]: r for r in labels if r.get("token_id")}

hist_by_token = {}
for r in history:
    token_id = r.get("token_id")
    if token_id:
        hist_by_token.setdefault(token_id, []).append(r)

token_rows = []

for token_id, rows in hist_by_token.items():
    label = labels_by_token.get(token_id, {})
    if not label:
        continue

    rows = sorted(rows, key=lambda r: fnum(r.get("history_timestamp")) or 0)
    prices = [fnum(r.get("price")) for r in rows]
    prices = [p for p in prices if p is not None]

    if not prices:
        continue

    target = fnum(label.get("target"))
    if target is None:
        continue

    first_price = prices[0]
    latest_price = prices[-1]

    token_rows.append({
        "fixture_slug": label.get("fixture_slug", ""),
        "fixture": label.get("fixture", ""),
        "condition_id": label.get("condition_id", ""),
        "question": label.get("question", ""),
        "market_slug": label.get("market_slug", ""),
        "market_index": label.get("market_index", ""),
        "team_or_draw": label.get("team_or_draw", ""),
        "outcome": label.get("outcome", ""),
        "token_id": token_id,
        "target": target,
        "history_rows": len(rows),
        "first_price": first_price,
        "latest_price": latest_price,
        "first_brier": (first_price - target) ** 2,
        "latest_brier": (latest_price - target) ** 2,
        "first_hit": int((first_price >= 0.5) == (target == 1)),
        "latest_hit": int((latest_price >= 0.5) == (target == 1)),
    })

# Moneyline-level validation uses YES rows only.
yes_rows = [r for r in token_rows if r["outcome"] == "Yes"]

by_fixture_snapshot = {}

for r in yes_rows:
    fixture_slug = r["fixture_slug"]
    snapshot_key = fixture_slug

    by_fixture_snapshot.setdefault(snapshot_key, []).append(r)

moneyline_rows = []

for fixture_slug, rows in by_fixture_snapshot.items():
    if len(rows) < 3:
        continue

    # Keep one row per market_index.
    by_market_index = {}
    for r in rows:
        by_market_index[r["market_index"]] = r

    if len(by_market_index) != 3:
        continue

    ordered = [by_market_index[str(i)] for i in range(3)]

    first_sum = sum(fnum(r["first_price"]) or 0 for r in ordered)
    latest_sum = sum(fnum(r["latest_price"]) or 0 for r in ordered)

    if first_sum <= 0 or latest_sum <= 0:
        continue

    first_probs = [(fnum(r["first_price"]) or 0) / first_sum for r in ordered]
    latest_probs = [(fnum(r["latest_price"]) or 0) / latest_sum for r in ordered]
    targets = [fnum(r["target"]) or 0 for r in ordered]

    first_brier = sum((p - t) ** 2 for p, t in zip(first_probs, targets))
    latest_brier = sum((p - t) ** 2 for p, t in zip(latest_probs, targets))

    first_pick_idx = max(range(3), key=lambda i: first_probs[i])
    latest_pick_idx = max(range(3), key=lambda i: latest_probs[i])
    actual_idx = max(range(3), key=lambda i: targets[i])

    moneyline_rows.append({
        "fixture_slug": fixture_slug,
        "fixture": ordered[0]["fixture"],
        "outcomes": "|".join(r["team_or_draw"] for r in ordered),
        "actual_outcome": ordered[actual_idx]["team_or_draw"],
        "first_pick": ordered[first_pick_idx]["team_or_draw"],
        "latest_pick": ordered[latest_pick_idx]["team_or_draw"],
        "first_hit": int(first_pick_idx == actual_idx),
        "latest_hit": int(latest_pick_idx == actual_idx),
        "first_probability_sum": first_sum,
        "latest_probability_sum": latest_sum,
        "first_brier_normalised": first_brier,
        "latest_brier_normalised": latest_brier,
        "first_probs": "|".join(str(x) for x in first_probs),
        "latest_probs": "|".join(str(x) for x in latest_probs),
    })

token_fields = [
    "fixture_slug",
    "fixture",
    "condition_id",
    "question",
    "market_slug",
    "market_index",
    "team_or_draw",
    "outcome",
    "token_id",
    "target",
    "history_rows",
    "first_price",
    "latest_price",
    "first_brier",
    "latest_brier",
    "first_hit",
    "latest_hit",
]

moneyline_fields = [
    "fixture_slug",
    "fixture",
    "outcomes",
    "actual_outcome",
    "first_pick",
    "latest_pick",
    "first_hit",
    "latest_hit",
    "first_probability_sum",
    "latest_probability_sum",
    "first_brier_normalised",
    "latest_brier_normalised",
    "first_probs",
    "latest_probs",
]

write_csv(OUT_TOKEN, token_rows, token_fields)
write_csv(OUT_MONEYLINE, moneyline_rows, moneyline_fields)

summary = {
    "history_rows": len(history),
    "label_rows": len(labels),
    "token_validation_rows": len(token_rows),
    "moneyline_validation_rows": len(moneyline_rows),
    "moneyline_fixtures": len({r["fixture_slug"] for r in moneyline_rows}),
    "first_hit_rate": (
        sum(r["first_hit"] for r in moneyline_rows) / len(moneyline_rows)
        if moneyline_rows else None
    ),
    "latest_hit_rate": (
        sum(r["latest_hit"] for r in moneyline_rows) / len(moneyline_rows)
        if moneyline_rows else None
    ),
    "mean_first_brier": (
        sum(fnum(r["first_brier_normalised"]) or 0 for r in moneyline_rows) / len(moneyline_rows)
        if moneyline_rows else None
    ),
    "mean_latest_brier": (
        sum(fnum(r["latest_brier_normalised"]) or 0 for r in moneyline_rows) / len(moneyline_rows)
        if moneyline_rows else None
    ),
    "token_output": str(OUT_TOKEN),
    "moneyline_output": str(OUT_MONEYLINE),
}

SUMMARY.parent.mkdir(parents=True, exist_ok=True)
SUMMARY.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
print(json.dumps(summary, indent=2, sort_keys=True))

