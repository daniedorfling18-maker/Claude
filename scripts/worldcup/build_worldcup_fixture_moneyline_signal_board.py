from __future__ import annotations

import csv
import json
from pathlib import Path
from datetime import datetime, timezone

INP = Path("outputs/polymarket_training/worldcup_fixture_moneyline_board.csv")

OUT = Path("outputs/polymarket_training/worldcup_fixture_moneyline_signal_board.csv")
SUMMARY = Path("outputs/polymarket_model_governance/worldcup_fixture_moneyline_signal_board_summary.json")

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

rows = read_csv(INP)
signals = []

for r in rows:
    usable = str(r.get("usable_for_signals", "")).lower() == "true"

    if not usable:
        continue

    confidence = fnum(r.get("confidence"))
    raw_sum = fnum(r.get("raw_probability_sum"))

    if confidence is None:
        continue

    if confidence >= 0.75:
        confidence_band = "strong_market_favourite"
    elif confidence >= 0.60:
        confidence_band = "clear_market_favourite"
    elif confidence >= 0.50:
        confidence_band = "moderate_market_favourite"
    elif confidence >= 0.40:
        confidence_band = "thin_edge_or_draw_risk"
    else:
        confidence_band = "low_conviction"

    signal = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "fixture_slug": r.get("fixture_slug", ""),
        "fixture": r.get("fixture", ""),
        "fixture_date": r.get("fixture_date", ""),
        "market_pick": r.get("predicted_outcome", ""),
        "market_confidence": confidence,
        "confidence_band": confidence_band,
        "raw_probability_sum": raw_sum,
        "outcome_1": r.get("outcome_1", ""),
        "outcome_1_probability": r.get("outcome_1_normalised_probability", ""),
        "outcome_2": r.get("outcome_2", ""),
        "outcome_2_probability": r.get("outcome_2_normalised_probability", ""),
        "outcome_3": r.get("outcome_3", ""),
        "outcome_3_probability": r.get("outcome_3_normalised_probability", ""),
        "quality_flags": r.get("quality_flags", ""),
        "signal_type": "market_implied_moneyline_baseline",
    }

    signals.append(signal)

OUT.parent.mkdir(parents=True, exist_ok=True)
SUMMARY.parent.mkdir(parents=True, exist_ok=True)

fieldnames = [
    "generated_at_utc",
    "fixture_slug",
    "fixture",
    "fixture_date",
    "market_pick",
    "market_confidence",
    "confidence_band",
    "raw_probability_sum",
    "outcome_1",
    "outcome_1_probability",
    "outcome_2",
    "outcome_2_probability",
    "outcome_3",
    "outcome_3_probability",
    "quality_flags",
    "signal_type",
]

with OUT.open("w", encoding="utf-8", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(signals)

summary = {
    "input_fixture_rows": len(rows),
    "signal_rows": len(signals),
    "excluded_rows": len(rows) - len(signals),
    "excluded_fixtures": [
        {
            "fixture_slug": r.get("fixture_slug", ""),
            "quality_flags": r.get("quality_flags", ""),
            "raw_probability_sum": r.get("raw_probability_sum", ""),
        }
        for r in rows
        if str(r.get("usable_for_signals", "")).lower() != "true"
    ],
    "output": str(OUT),
    "note": "Clean market-implied moneyline baseline. Excludes fixtures failing probability-sum or extraction-quality gates.",
}

SUMMARY.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
print(json.dumps(summary, indent=2, sort_keys=True))
