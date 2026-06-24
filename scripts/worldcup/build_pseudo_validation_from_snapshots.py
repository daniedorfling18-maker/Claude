from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from statistics import mean

PSEUDO = Path("outputs/polymarket_training/pseudo_label_candidates.csv")

SNAPSHOT_FILES = [
    Path("outputs/polymarket/ml/raw_market_snapshots.csv"),
    Path("outputs/polymarket_wide/worldcup/ml/raw_market_snapshots.csv"),
    Path("outputs/polymarket_wide/sports/ml/raw_market_snapshots.csv"),
    Path("outputs/polymarket_wide/all/ml/raw_market_snapshots.csv"),
]

OUT = Path("outputs/polymarket_training/pseudo_validation_snapshots.csv")
SUMMARY = Path("outputs/polymarket_model_governance/pseudo_validation_snapshot_summary.json")

def read_csv(path: Path):
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as f:
        return list(csv.DictReader(f))

def rows_stream(path: Path):
    if not path.exists() or path.stat().st_size == 0:
        return
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            yield {str(k): "" if v is None else str(v) for k, v in row.items()}

def first(row, names):
    for n in names:
        v = row.get(n)
        if v is not None and str(v).strip():
            return str(v).strip()
    return ""

def fnum(value):
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None

def clip(p, eps=1e-6):
    return min(max(p, eps), 1.0 - eps)

pseudo_rows = read_csv(PSEUDO)

pseudo_by_token = {}
for r in pseudo_rows:
    token = first(r, ["token_id", "asset_id", "clob_token_id", "token"])
    if token:
        pseudo_by_token[token] = r

wanted_tokens = set(pseudo_by_token)

print(f"pseudo_label_rows: {len(pseudo_rows)}")
print(f"wanted_tokens: {len(wanted_tokens)}")

joined = []
scanned_rows = 0
matched_rows = 0

for path in SNAPSHOT_FILES:
    if not path.exists():
        print(f"missing: {path}")
        continue

    print(f"scanning: {path}")

    for row in rows_stream(path) or []:
        scanned_rows += 1

        token = first(row, ["token_id", "asset_id", "clob_token_id", "token"])
        if token not in wanted_tokens:
            continue

        matched_rows += 1
        label = pseudo_by_token[token]

        target = fnum(label.get("target"))
        implied = fnum(
            first(row, [
                "implied_probability",
                "midpoint",
                "price",
                "last_trade_price",
                "best_ask",
                "best_bid",
            ])
        )

        brier = ""
        log_loss = ""

        if target is not None and implied is not None:
            p = clip(implied)
            brier = (p - target) ** 2
            log_loss = -(target * math.log(p) + (1 - target) * math.log(1 - p))

        joined.append({
            "source_file": str(path),
            "snapshot_timestamp": first(row, [
                "snapshot_timestamp",
                "timestamp",
                "collected_at_utc",
                "source_timestamp",
                "updated_at",
                "created_at",
            ]),
            "market_id": first(row, ["market_id", "market_slug", "slug", "market", "condition_id"]),
            "token_id": token,
            "question": first(row, ["question", "title", "description"]),
            "outcome_snapshot": first(row, ["outcome", "outcome_name", "name"]),
            "best_bid": first(row, ["best_bid", "bid"]),
            "best_ask": first(row, ["best_ask", "ask"]),
            "midpoint": first(row, ["midpoint"]),
            "price": first(row, ["price", "last_trade_price"]),
            "liquidity": first(row, ["liquidity", "liquidityNum"]),
            "volume": first(row, ["volume", "volumeNum"]),
            "pseudo_market_slug": label.get("market_slug", ""),
            "pseudo_question": label.get("question_gamma", ""),
            "pseudo_outcome": label.get("outcome", ""),
            "pseudo_target": label.get("target", ""),
            "pseudo_confidence": label.get("confidence", ""),
            "pseudo_outcome_price": label.get("outcome_price", ""),
            "pseudo_brier": brier,
            "pseudo_log_loss": log_loss,
        })

OUT.parent.mkdir(parents=True, exist_ok=True)
SUMMARY.parent.mkdir(parents=True, exist_ok=True)

fieldnames = [
    "source_file",
    "snapshot_timestamp",
    "market_id",
    "token_id",
    "question",
    "outcome_snapshot",
    "best_bid",
    "best_ask",
    "midpoint",
    "price",
    "liquidity",
    "volume",
    "pseudo_market_slug",
    "pseudo_question",
    "pseudo_outcome",
    "pseudo_target",
    "pseudo_confidence",
    "pseudo_outcome_price",
    "pseudo_brier",
    "pseudo_log_loss",
]

with OUT.open("w", encoding="utf-8", newline="") as f:
    w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
    w.writeheader()
    w.writerows(joined)

briers = [fnum(r.get("pseudo_brier")) for r in joined]
briers = [x for x in briers if x is not None]

log_losses = [fnum(r.get("pseudo_log_loss")) for r in joined]
log_losses = [x for x in log_losses if x is not None]

summary = {
    "pseudo_label_rows": len(pseudo_rows),
    "wanted_tokens": len(wanted_tokens),
    "snapshot_rows_scanned": scanned_rows,
    "matched_snapshot_rows": matched_rows,
    "joined_rows": len(joined),
    "joined_tokens": len({r["token_id"] for r in joined}),
    "joined_markets": len({r["pseudo_market_slug"] for r in joined}),
    "mean_pseudo_brier": mean(briers) if briers else None,
    "mean_pseudo_log_loss": mean(log_losses) if log_losses else None,
    "output": str(OUT),
    "warning": "Pseudo labels are near-terminal active-market labels. Do not use for official calibration training.",
}

SUMMARY.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
print(json.dumps(summary, indent=2, sort_keys=True))
