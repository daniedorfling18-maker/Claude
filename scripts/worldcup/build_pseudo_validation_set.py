from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from statistics import mean

FEATURES = Path("outputs/polymarket_training/features_v2.csv")
PSEUDO = Path("outputs/polymarket_training/pseudo_label_candidates.csv")

OUT = Path("outputs/polymarket_training/pseudo_validation_features.csv")
SUMMARY = Path("outputs/polymarket_model_governance/pseudo_validation_summary.json")

def read_csv(path: Path):
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as f:
        return list(csv.DictReader(f))

def fnum(value):
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None

def clip(p, eps=1e-6):
    return min(max(p, eps), 1.0 - eps)

features = read_csv(FEATURES)
pseudo = read_csv(PSEUDO)

pseudo_by_token = {}
for r in pseudo:
    token = (r.get("token_id") or "").strip()
    if not token:
        continue
    pseudo_by_token[token] = r

joined = []

for row in features:
    token = (row.get("token_id") or row.get("asset_id") or "").strip()
    if token not in pseudo_by_token:
        continue

    label = pseudo_by_token[token]

    target = fnum(label.get("target"))
    model_p = fnum(
        row.get("predicted_probability")
        or row.get("model_probability")
        or row.get("implied_probability")
        or row.get("midpoint")
    )

    brier = ""
    log_loss = ""

    if target is not None and model_p is not None:
        p = clip(model_p)
        brier = (p - target) ** 2
        log_loss = -(target * math.log(p) + (1 - target) * math.log(1 - p))

    joined.append({
        **row,
        "pseudo_target": label.get("target", ""),
        "pseudo_label_confidence": label.get("confidence", ""),
        "pseudo_outcome_price": label.get("outcome_price", ""),
        "pseudo_outcome": label.get("outcome", ""),
        "pseudo_market_slug": label.get("market_slug", ""),
        "pseudo_question": label.get("question_gamma", ""),
        "pseudo_label_status": label.get("label_status", ""),
        "pseudo_brier": brier,
        "pseudo_log_loss": log_loss,
    })

OUT.parent.mkdir(parents=True, exist_ok=True)
SUMMARY.parent.mkdir(parents=True, exist_ok=True)

fieldnames = []
for row in joined:
    for k in row.keys():
        if k not in fieldnames:
            fieldnames.append(k)

with OUT.open("w", encoding="utf-8", newline="") as f:
    w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
    w.writeheader()
    w.writerows(joined)

briers = [fnum(r.get("pseudo_brier")) for r in joined]
briers = [x for x in briers if x is not None]

log_losses = [fnum(r.get("pseudo_log_loss")) for r in joined]
log_losses = [x for x in log_losses if x is not None]

summary = {
    "features_rows": len(features),
    "pseudo_label_rows": len(pseudo),
    "joined_rows": len(joined),
    "joined_tokens": len({r.get("token_id") or r.get("asset_id") for r in joined}),
    "joined_markets": len({r.get("pseudo_market_slug") for r in joined}),
    "mean_pseudo_brier": mean(briers) if briers else None,
    "mean_pseudo_log_loss": mean(log_losses) if log_losses else None,
    "output": str(OUT),
    "warning": "Pseudo labels are near-terminal active-market labels. Do not use for official calibration training.",
}

SUMMARY.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")

print(json.dumps(summary, indent=2, sort_keys=True))
