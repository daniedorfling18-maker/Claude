from __future__ import annotations

import csv
import json
from pathlib import Path
from collections import Counter

LABELS = Path("outputs/polymarket_training/completed_game_labels_2026_06_23.csv")

SNAPSHOT_FILES = [
    Path("outputs/polymarket/ml/raw_market_snapshots.csv"),
    Path("outputs/polymarket_wide/worldcup/ml/raw_market_snapshots.csv"),
    Path("outputs/polymarket_wide/sports/ml/raw_market_snapshots.csv"),
    Path("outputs/polymarket_wide/all/ml/raw_market_snapshots.csv"),
    Path("outputs/polymarket_training/features.csv"),
    Path("outputs/polymarket_training/features_v2.csv"),
]

OUT = Path("outputs/polymarket_training/completed_game_snapshot_join_2026_06_23.csv")
SUMMARY = Path("outputs/polymarket_model_governance/completed_game_snapshot_join_2026_06_23_summary.json")

def read_csv(path):
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as f:
        return list(csv.DictReader(f))

def iter_csv(path):
    if not path.exists() or path.stat().st_size == 0:
        return
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as f:
        yield from csv.DictReader(f)

def first(row, names):
    for n in names:
        v = row.get(n)
        if v is not None and str(v).strip():
            return str(v).strip()
    return ""

labels = read_csv(LABELS)
labels_by_token = {r["token_id"]: r for r in labels}
wanted = set(labels_by_token)

joined = []
scanned_counts = {}
matched_counts = {}

for path in SNAPSHOT_FILES:
    scanned = 0
    matched = 0

    for row in iter_csv(path) or []:
        scanned += 1

        token = first(row, ["token_id", "asset_id", "clob_token_id", "token"])
        if token not in wanted:
            continue

        matched += 1
        label = labels_by_token[token]

        out = {
            "source_file": str(path),
            "token_id": token,
            "fixture": label.get("fixture", ""),
            "fixture_slug": label.get("fixture_slug", ""),
            "market_type": label.get("market_type", ""),
            "market_question": label.get("market_question", ""),
            "outcome": label.get("outcome", ""),
            "target": label.get("target", ""),
            "resolved_outcome_price": label.get("resolved_outcome_price", ""),
            "snapshot_timestamp": first(row, [
                "snapshot_timestamp",
                "timestamp",
                "collected_at_utc",
                "source_timestamp",
                "updated_at",
                "created_at",
            ]),
            "market_slug": first(row, ["market_slug", "slug", "market"]),
            "question": first(row, ["question", "title", "description"]),
            "snapshot_outcome": first(row, ["outcome", "outcome_name", "name"]),
            "price": first(row, ["price", "last_trade_price"]),
            "midpoint": first(row, ["midpoint"]),
            "best_bid": first(row, ["best_bid", "bid"]),
            "best_ask": first(row, ["best_ask", "ask"]),
            "volume": first(row, ["volume", "volumeNum"]),
            "liquidity": first(row, ["liquidity", "liquidityNum"]),
        }

        joined.append(out)

    scanned_counts[str(path)] = scanned
    matched_counts[str(path)] = matched

OUT.parent.mkdir(parents=True, exist_ok=True)
SUMMARY.parent.mkdir(parents=True, exist_ok=True)

fieldnames = [
    "source_file",
    "token_id",
    "fixture",
    "fixture_slug",
    "market_type",
    "market_question",
    "outcome",
    "target",
    "resolved_outcome_price",
    "snapshot_timestamp",
    "market_slug",
    "question",
    "snapshot_outcome",
    "price",
    "midpoint",
    "best_bid",
    "best_ask",
    "volume",
    "liquidity",
]

with OUT.open("w", encoding="utf-8", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(joined)

summary = {
    "label_rows": len(labels),
    "label_tokens": len(wanted),
    "joined_rows": len(joined),
    "joined_tokens": len({r["token_id"] for r in joined}),
    "joined_by_source_file": matched_counts,
    "scanned_by_source_file": scanned_counts,
    "missing_label_tokens": sorted(wanted - {r["token_id"] for r in joined}),
    "output": str(OUT),
    "next_step": "If joined_rows > 0, build completed-game validation report. If 0, collect historical/current CLOB prices for these six tokens.",
}

SUMMARY.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
print(json.dumps(summary, indent=2, sort_keys=True))
