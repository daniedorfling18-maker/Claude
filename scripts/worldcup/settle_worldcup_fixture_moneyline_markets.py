from __future__ import annotations

import csv
import json
from pathlib import Path
from datetime import datetime, timezone

MARKETS = Path("outputs/polymarket_model_governance/worldcup_fixture_moneyline_market_inventory.csv")
SCORES = Path("outputs/polymarket_training/worldcup_fixture_final_scores.csv")

OUT = Path("outputs/polymarket_training/worldcup_completed_moneyline_labels.csv")
SUMMARY = Path("outputs/polymarket_model_governance/worldcup_completed_moneyline_labels_summary.json")

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def read_csv(path):
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as f:
        return list(csv.DictReader(f))

def parse_int(x):
    try:
        if x is None or str(x).strip() == "":
            return None
        return int(str(x).strip())
    except Exception:
        return None

def parse_json_array(x):
    try:
        return json.loads(x)
    except Exception:
        return []

markets = read_csv(MARKETS)
scores = read_csv(SCORES)

scores_by_slug = {
    r["fixture_slug"]: r
    for r in scores
    if r.get("fixture_slug")
}

labels = []
skipped = []

for m in markets:
    slug = m.get("fixture_slug", "")
    score = scores_by_slug.get(slug)

    if not score:
        skipped.append({
            "fixture_slug": slug,
            "reason": "missing_score_row",
        })
        continue

    if score.get("final_score_status", "").strip().lower() != "complete":
        skipped.append({
            "fixture_slug": slug,
            "reason": "score_not_complete",
        })
        continue

    team_a_goals = parse_int(score.get("team_a_goals"))
    team_b_goals = parse_int(score.get("team_b_goals"))

    if team_a_goals is None or team_b_goals is None:
        skipped.append({
            "fixture_slug": slug,
            "reason": "missing_goals",
        })
        continue

    market_index = str(m.get("market_index", "")).strip()
    market_type = m.get("market_type", "")
    team_or_draw = m.get("team_or_draw", "")

    if market_index == "0":
        yes_target = 1 if team_a_goals > team_b_goals else 0
        resolved_outcome = score.get("team_a", "")
    elif market_index == "1":
        yes_target = 1 if team_a_goals == team_b_goals else 0
        resolved_outcome = "Draw"
    elif market_index == "2":
        yes_target = 1 if team_b_goals > team_a_goals else 0
        resolved_outcome = score.get("team_b", "")
    else:
        skipped.append({
            "fixture_slug": slug,
            "reason": f"unknown_market_index_{market_index}",
        })
        continue

    token_ids = parse_json_array(m.get("clob_token_ids_raw", "[]"))
    outcome_prices = parse_json_array(m.get("outcome_prices_raw", "[]"))

    if len(token_ids) != 2:
        skipped.append({
            "fixture_slug": slug,
            "reason": "token_id_array_not_two",
        })
        continue

    outcomes = ["Yes", "No"]
    targets = [yes_target, 1 - yes_target]

    for i, outcome in enumerate(outcomes):
        labels.append({
            "label_generated_at_utc": now_iso(),
            "fixture_slug": slug,
            "fixture_date": m.get("fixture_date", ""),
            "fixture_url": m.get("fixture_url", ""),
            "team_a": score.get("team_a", ""),
            "team_b": score.get("team_b", ""),
            "team_a_goals": team_a_goals,
            "team_b_goals": team_b_goals,
            "final_score": f'{score.get("team_a", "")} {team_a_goals}-{team_b_goals} {score.get("team_b", "")}',
            "resolved_match_outcome": (
                score.get("team_a", "") if team_a_goals > team_b_goals
                else score.get("team_b", "") if team_b_goals > team_a_goals
                else "Draw"
            ),
            "market_index": market_index,
            "market_type": market_type,
            "team_or_draw": team_or_draw,
            "question": m.get("question", ""),
            "condition_id": m.get("condition_id", ""),
            "outcome": outcome,
            "target": targets[i],
            "token_id": token_ids[i],
            "page_outcome_price_at_collection": outcome_prices[i] if i < len(outcome_prices) else "",
            "score_source": score.get("source", ""),
            "notes": score.get("notes", ""),
        })

OUT.parent.mkdir(parents=True, exist_ok=True)
SUMMARY.parent.mkdir(parents=True, exist_ok=True)

fieldnames = [
    "label_generated_at_utc",
    "fixture_slug",
    "fixture_date",
    "fixture_url",
    "team_a",
    "team_b",
    "team_a_goals",
    "team_b_goals",
    "final_score",
    "resolved_match_outcome",
    "market_index",
    "market_type",
    "team_or_draw",
    "question",
    "condition_id",
    "outcome",
    "target",
    "token_id",
    "page_outcome_price_at_collection",
    "score_source",
    "notes",
]

with OUT.open("w", encoding="utf-8", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(labels)

completed_score_rows = [
    r for r in scores
    if r.get("final_score_status", "").strip().lower() == "complete"
]

summary = {
    "market_rows": len(markets),
    "score_rows": len(scores),
    "completed_score_rows": len(completed_score_rows),
    "label_rows": len(labels),
    "labelled_fixtures": len({r["fixture_slug"] for r in labels}),
    "skipped_count": len(skipped),
    "skipped_reasons": sorted({r["reason"] for r in skipped}),
    "output": str(OUT),
    "status": "ok",
    "note": "Creates token-level Yes/No settlement labels from completed fixture scores.",
}

SUMMARY.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
print(json.dumps(summary, indent=2, sort_keys=True))
