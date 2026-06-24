from __future__ import annotations

import csv
import json
from pathlib import Path
from datetime import datetime, timezone

INP = Path("outputs/polymarket_training/worldcup_fixture_moneyline_signal_board.csv")

OUT = Path("outputs/polymarket_training/worldcup_superbru_market_baseline_picks.csv")
SUMMARY = Path("outputs/polymarket_model_governance/worldcup_superbru_market_baseline_picks_summary.json")

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

def scoreline_for(row):
    pick = row["market_pick"]
    conf = fnum(row["market_confidence"]) or 0

    outcome_1 = row["outcome_1"]
    outcome_2 = row["outcome_2"]
    outcome_3 = row["outcome_3"]

    p1 = fnum(row["outcome_1_probability"]) or 0
    pd = fnum(row["outcome_2_probability"]) or 0
    p3 = fnum(row["outcome_3_probability"]) or 0

    # Assumes outcome_2 is Draw, as produced by our board.
    if pick == "Draw":
        if pd >= 0.38:
            return "1-1"
        return "0-0"

    # Team in outcome_1 wins.
    if pick == outcome_1:
        if conf >= 0.80 and p3 <= 0.08:
            return "3-0"
        if conf >= 0.70:
            return "2-0"
        if pd >= 0.25:
            return "1-0"
        return "2-1"

    # Team in outcome_3 wins.
    if pick == outcome_3:
        if conf >= 0.80 and p1 <= 0.08:
            return "0-3"
        if conf >= 0.70:
            return "0-2"
        if pd >= 0.25:
            return "0-1"
        return "1-2"

    return ""

rows = read_csv(INP)
out = []

for r in rows:
    conf = fnum(r.get("market_confidence")) or 0
    pick = r.get("market_pick", "")

    if conf >= 0.75:
        risk = "low"
    elif conf >= 0.60:
        risk = "medium"
    elif conf >= 0.50:
        risk = "medium_high"
    else:
        risk = "high"

    out.append({
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "fixture_slug": r.get("fixture_slug", ""),
        "fixture_date": r.get("fixture_date", ""),
        "market_pick": pick,
        "market_confidence": conf,
        "confidence_band": r.get("confidence_band", ""),
        "risk_band": risk,
        "baseline_scoreline": scoreline_for(r),
        "outcome_1": r.get("outcome_1", ""),
        "outcome_1_probability": r.get("outcome_1_probability", ""),
        "outcome_2": r.get("outcome_2", ""),
        "outcome_2_probability": r.get("outcome_2_probability", ""),
        "outcome_3": r.get("outcome_3", ""),
        "outcome_3_probability": r.get("outcome_3_probability", ""),
        "source": "polymarket_market_implied_baseline",
        "note": "Scoreline is a heuristic placeholder from 3-way probabilities, not yet a trained score model.",
    })

OUT.parent.mkdir(parents=True, exist_ok=True)
SUMMARY.parent.mkdir(parents=True, exist_ok=True)

fieldnames = [
    "generated_at_utc",
    "fixture_slug",
    "fixture_date",
    "market_pick",
    "market_confidence",
    "confidence_band",
    "risk_band",
    "baseline_scoreline",
    "outcome_1",
    "outcome_1_probability",
    "outcome_2",
    "outcome_2_probability",
    "outcome_3",
    "outcome_3_probability",
    "source",
    "note",
]

with OUT.open("w", encoding="utf-8", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(out)

summary = {
    "input_signal_rows": len(rows),
    "pick_rows": len(out),
    "output": str(OUT),
    "warning": "These are market-implied baseline picks. Scorelines are heuristic placeholders and should not be treated as trained predictions.",
}

SUMMARY.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
print(json.dumps(summary, indent=2, sort_keys=True))
