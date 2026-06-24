from __future__ import annotations

import csv
import json
from pathlib import Path

INP = Path("outputs/polymarket_training/worldcup_superbru_final_picks_v0_market_baseline.csv")

OUT = Path("outputs/polymarket_training/worldcup_superbru_review_triage.csv")
SUMMARY = Path("outputs/polymarket_model_governance/worldcup_superbru_review_triage_summary.json")

def read_csv(path):
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
out = []

for r in rows:
    conf = fnum(r.get("final_confidence")) or 0
    pick = r.get("final_pick", "")
    score = r.get("final_scoreline", "")

    needs_review = False
    review_reason = []

    if conf < 0.50:
        needs_review = True
        review_reason.append("market_pick_below_50_percent")

    if conf < 0.60:
        needs_review = True
        review_reason.append("low_to_moderate_confidence")

    if pick == "Draw":
        needs_review = True
        review_reason.append("draw_pick_requires_manual_check")

    if score in {"1-0", "0-1", "1-1", "1-2", "2-1"}:
        review_reason.append("narrow_scoreline")

    review_priority = "low"
    if needs_review and conf < 0.45:
        review_priority = "high"
    elif needs_review:
        review_priority = "medium"

    out.append({
        "fixture_slug": r.get("fixture_slug", ""),
        "fixture_date": r.get("fixture_date", ""),
        "baseline_pick": pick,
        "baseline_scoreline": score,
        "baseline_confidence": conf,
        "needs_manual_review": needs_review,
        "review_priority": review_priority,
        "review_reason": "|".join(review_reason) if review_reason else "baseline_ok",
        "recommended_action": "research_before_override" if needs_review else "accept_market_baseline_unless_new_info",
    })

OUT.parent.mkdir(parents=True, exist_ok=True)
SUMMARY.parent.mkdir(parents=True, exist_ok=True)

fieldnames = [
    "fixture_slug",
    "fixture_date",
    "baseline_pick",
    "baseline_scoreline",
    "baseline_confidence",
    "needs_manual_review",
    "review_priority",
    "review_reason",
    "recommended_action",
]

with OUT.open("w", encoding="utf-8", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(out)

summary = {
    "input_rows": len(rows),
    "review_rows": len(out),
    "manual_review_required": sum(1 for r in out if r["needs_manual_review"]),
    "high_priority_reviews": sum(1 for r in out if r["review_priority"] == "high"),
    "medium_priority_reviews": sum(1 for r in out if r["review_priority"] == "medium"),
    "low_priority_reviews": sum(1 for r in out if r["review_priority"] == "low"),
    "output": str(OUT),
}

SUMMARY.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
print(json.dumps(summary, indent=2, sort_keys=True))
