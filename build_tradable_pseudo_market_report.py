from __future__ import annotations

import csv
import json
from pathlib import Path
from statistics import mean

INP = Path("outputs/polymarket_training/tradable_pseudo_validation_latest.csv")
OUT = Path("outputs/polymarket_training/tradable_pseudo_market_report.csv")
SUMMARY = Path("outputs/polymarket_model_governance/tradable_pseudo_market_report_summary.json")

def fnum(x):
    try:
        if x is None or x == "":
            return None
        return float(x)
    except Exception:
        return None

def market_prob(row):
    midpoint = fnum(row.get("midpoint"))
    if midpoint is not None:
        return midpoint

    bid = fnum(row.get("best_bid"))
    ask = fnum(row.get("best_ask"))
    if bid is not None and ask is not None:
        return (bid + ask) / 2

    return fnum(row.get("price"))

with INP.open("r", encoding="utf-8-sig", errors="replace", newline="") as f:
    rows = list(csv.DictReader(f))

by_market = {}
for r in rows:
    slug = r.get("pseudo_market_slug", "")
    by_market.setdefault(slug, []).append(r)

market_rows = []

for slug, group in by_market.items():
    # Identify pseudo winner / loser rows.
    winner_rows = [r for r in group if str(r.get("pseudo_target")) == "1"]
    loser_rows = [r for r in group if str(r.get("pseudo_target")) == "0"]

    if not winner_rows:
        continue

    winner = winner_rows[0]
    winner_prob = market_prob(winner)
    winner_terminal_price = fnum(winner.get("pseudo_outcome_price"))
    winner_brier = fnum(winner.get("pseudo_brier"))

    loser_probs = [market_prob(r) for r in loser_rows]
    loser_probs = [p for p in loser_probs if p is not None]

    market_rows.append({
        "market_slug": slug,
        "question": winner.get("pseudo_question", ""),
        "winning_outcome": winner.get("pseudo_outcome", ""),
        "winner_token_id": winner.get("token_id", ""),
        "winner_snapshot_probability": winner_prob,
        "winner_terminal_probability": winner_terminal_price,
        "winner_gap_to_terminal": None if winner_prob is None or winner_terminal_price is None else winner_terminal_price - winner_prob,
        "winner_pseudo_brier": winner_brier,
        "loser_probability_mean": mean(loser_probs) if loser_probs else "",
        "token_rows": len(group),
        "source_files": "|".join(sorted({r.get("source_file", "") for r in group})),
    })

market_rows.sort(
    key=lambda r: (
        abs(float(r["winner_gap_to_terminal"])) if r["winner_gap_to_terminal"] not in ("", None) else 999,
        r["market_slug"],
    )
)

OUT.parent.mkdir(parents=True, exist_ok=True)
SUMMARY.parent.mkdir(parents=True, exist_ok=True)

with OUT.open("w", encoding="utf-8", newline="") as f:
    fieldnames = [
        "market_slug",
        "question",
        "winning_outcome",
        "winner_token_id",
        "winner_snapshot_probability",
        "winner_terminal_probability",
        "winner_gap_to_terminal",
        "winner_pseudo_brier",
        "loser_probability_mean",
        "token_rows",
        "source_files",
    ]
    w = csv.DictWriter(f, fieldnames=fieldnames)
    w.writeheader()
    w.writerows(market_rows)

briers = [fnum(r.get("winner_pseudo_brier")) for r in market_rows]
briers = [b for b in briers if b is not None]

summary = {
    "input_token_rows": len(rows),
    "market_rows": len(market_rows),
    "mean_winner_pseudo_brier": mean(briers) if briers else None,
    "output": str(OUT),
    "warning": "Pseudo labels are near-terminal active-market labels. Do not use for official calibration training.",
}

SUMMARY.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
print(json.dumps(summary, indent=2, sort_keys=True))
