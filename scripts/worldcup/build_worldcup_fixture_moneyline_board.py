from __future__ import annotations

import csv
import json
from pathlib import Path

INP = Path("outputs/polymarket_training/worldcup_fixture_moneyline_clob_prices.csv")

OUT = Path("outputs/polymarket_training/worldcup_fixture_moneyline_board.csv")
SUMMARY = Path("outputs/polymarket_model_governance/worldcup_fixture_moneyline_board_summary.json")

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

yes_rows = [r for r in rows if r.get("outcome") == "Yes"]

by_fixture = {}
for r in yes_rows:
    by_fixture.setdefault(r["fixture_slug"], []).append(r)

board = []

for fixture_slug, fx_rows in sorted(by_fixture.items()):
    probs = []

    for r in fx_rows:
        p = fnum(r.get("effective_price"))
        probs.append({
            "team_or_draw": r.get("team_or_draw", ""),
            "price": p,
            "fixture_date": r.get("fixture_date", ""),
            "fixture": r.get("fixture", ""),
            "fixture_slug": fixture_slug,
            "question": r.get("question", ""),
            "extraction_quality": r.get("extraction_quality", ""),
        })

    valid_probs = [x for x in probs if x["price"] is not None]
    raw_sum = sum(x["price"] for x in valid_probs)

    quality_flags = []

    if len(valid_probs) != 3:
        quality_flags.append("missing_moneyline_leg")

    if raw_sum < 0.95 or raw_sum > 1.05:
        quality_flags.append("probability_sum_outlier")

    if any(x["extraction_quality"] != "ok" for x in valid_probs):
        quality_flags.append("extraction_warning")

    usable_for_signals = not quality_flags

    if raw_sum > 0:
        for x in valid_probs:
            x["normalised_probability"] = x["price"] / raw_sum
    else:
        for x in valid_probs:
            x["normalised_probability"] = None

    predicted = ""
    confidence = ""

    if valid_probs:
        top = max(valid_probs, key=lambda x: x["normalised_probability"] or -1)
        predicted = top["team_or_draw"]
        confidence = top["normalised_probability"]

    # Flatten the 3 outcomes into columns.
    outcome_map = {x["team_or_draw"]: x for x in valid_probs}

    teams = [x["team_or_draw"] for x in valid_probs]

    board.append({
        "fixture_slug": fixture_slug,
        "fixture": valid_probs[0]["fixture"] if valid_probs else fixture_slug,
        "fixture_date": valid_probs[0]["fixture_date"] if valid_probs else "",
        "outcome_1": teams[0] if len(teams) > 0 else "",
        "outcome_1_raw_probability": valid_probs[0]["price"] if len(valid_probs) > 0 else "",
        "outcome_1_normalised_probability": valid_probs[0]["normalised_probability"] if len(valid_probs) > 0 else "",
        "outcome_2": teams[1] if len(teams) > 1 else "",
        "outcome_2_raw_probability": valid_probs[1]["price"] if len(valid_probs) > 1 else "",
        "outcome_2_normalised_probability": valid_probs[1]["normalised_probability"] if len(valid_probs) > 1 else "",
        "outcome_3": teams[2] if len(teams) > 2 else "",
        "outcome_3_raw_probability": valid_probs[2]["price"] if len(valid_probs) > 2 else "",
        "outcome_3_normalised_probability": valid_probs[2]["normalised_probability"] if len(valid_probs) > 2 else "",
        "raw_probability_sum": raw_sum,
        "predicted_outcome": predicted,
        "confidence": confidence,
        "usable_for_signals": usable_for_signals,
        "quality_flags": "|".join(quality_flags) if quality_flags else "ok",
    })

OUT.parent.mkdir(parents=True, exist_ok=True)
SUMMARY.parent.mkdir(parents=True, exist_ok=True)

fieldnames = [
    "fixture_slug",
    "fixture",
    "fixture_date",
    "outcome_1",
    "outcome_1_raw_probability",
    "outcome_1_normalised_probability",
    "outcome_2",
    "outcome_2_raw_probability",
    "outcome_2_normalised_probability",
    "outcome_3",
    "outcome_3_raw_probability",
    "outcome_3_normalised_probability",
    "raw_probability_sum",
    "predicted_outcome",
    "confidence",
    "usable_for_signals",
    "quality_flags",
]

with OUT.open("w", encoding="utf-8", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(board)

summary = {
    "input_yes_rows": len(yes_rows),
    "fixture_rows": len(board),
    "usable_for_signals": sum(1 for r in board if r["usable_for_signals"]),
    "flagged_rows": sum(1 for r in board if not r["usable_for_signals"]),
    "flagged_fixtures": [
        {
            "fixture_slug": r["fixture_slug"],
            "raw_probability_sum": r["raw_probability_sum"],
            "quality_flags": r["quality_flags"],
        }
        for r in board
        if not r["usable_for_signals"]
    ],
    "output": str(OUT),
    "note": "A fixture is usable only if all three Yes outcomes exist and raw probabilities sum between 0.95 and 1.05.",
}

SUMMARY.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
print(json.dumps(summary, indent=2, sort_keys=True))
