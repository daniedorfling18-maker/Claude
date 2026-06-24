from __future__ import annotations

import csv
import json
from pathlib import Path
from statistics import mean

INP = Path("outputs/polymarket_training/pseudo_validation_snapshots.csv")
OUT = Path("outputs/polymarket_training/tradable_pseudo_validation_snapshots.csv")
SUMMARY = Path("outputs/polymarket_model_governance/tradable_pseudo_validation_summary.json")

def fnum(x):
    try:
        if x is None or x == "":
            return None
        return float(x)
    except Exception:
        return None

with INP.open("r", encoding="utf-8-sig", errors="replace", newline="") as f:
    rows = list(csv.DictReader(f))

filtered = []

for r in rows:
    confidence = fnum(r.get("pseudo_confidence"))
    outcome_price = fnum(r.get("pseudo_outcome_price"))
    midpoint = fnum(r.get("midpoint"))
    best_bid = fnum(r.get("best_bid"))
    best_ask = fnum(r.get("best_ask"))

    market_prob = midpoint
    if market_prob is None and best_bid is not None and best_ask is not None:
        market_prob = (best_bid + best_ask) / 2
    if market_prob is None:
        market_prob = fnum(r.get("price"))

    if confidence is None or outcome_price is None or market_prob is None:
        continue

    # Exclude totally dead/certain tails; keep near-terminal but still informative rows.
    if not (0.97 <= confidence <= 0.995):
        continue
    if not (0.02 <= market_prob <= 0.98):
        continue

    filtered.append(r)

OUT.parent.mkdir(parents=True, exist_ok=True)
SUMMARY.parent.mkdir(parents=True, exist_ok=True)

with OUT.open("w", encoding="utf-8", newline="") as f:
    if filtered:
        w = csv.DictWriter(f, fieldnames=list(filtered[0].keys()), extrasaction="ignore")
        w.writeheader()
        w.writerows(filtered)
    else:
        f.write("")

summary = {
    "input_rows": len(rows),
    "tradable_rows": len(filtered),
    "tradable_tokens": len({r.get("token_id") for r in filtered}),
    "tradable_markets": len({r.get("pseudo_market_slug") for r in filtered}),
    "mean_pseudo_confidence": mean([fnum(r.get("pseudo_confidence")) for r in filtered if fnum(r.get("pseudo_confidence")) is not None]) if filtered else None,
    "output": str(OUT),
    "note": "Filtered pseudo-validation rows exclude ultra-certain tails and retain near-terminal but still tradable prices.",
}

SUMMARY.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
print(json.dumps(summary, indent=2, sort_keys=True))
